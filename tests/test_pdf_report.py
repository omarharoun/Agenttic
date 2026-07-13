"""PDF scorecard export: renders a real PDF, handles the all-errored case, and
the endpoint is auth-gated + tenant-scoped."""

from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.reporting.pdf_report import render_pdf
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.server.app import create_app

RUBRIC = Rubric(rubric_id="r-1", criteria=[
    Criterion(criterion_id="routing", description="Routes to the correct queue",
              scorer="code", scale="binary", check_ref="final_output_matches_expected"),
    Criterion(criterion_id="tone", description="Professional tone",
              scorer="judge", scale="three_point", anchors={"pass": "p", "fail": "f"}),
])


def _scorecard(sid="sc-1", errored=False):
    if errored:
        runs = [RunScore(trace_id=f"t{i}", test_id=f"e-{i}", criterion_scores=[],
                         passed=False, scoring_error="CheckConfigError: needs forbidden_tools")
                for i in range(3)]
    else:
        runs = [RunScore(
            trace_id=f"t{i}", test_id=f"tc-{i}", passed=(i != 2),
            criterion_scores=[
                CriterionScore(criterion_id="routing", score=1.0 if i != 2 else 0.0, scorer="code"),
                CriterionScore(criterion_id="tone", score=1.0, scorer="judge",
                               calibrated=False, judge_rationale="Slightly curt — fix tone."),
            ], cost_usd=0.01 * (i + 1), latency_ms=100.0 * (i + 1), steps=i + 2)
            for i in range(3)]
    return Scorecard.aggregate(
        scorecard_id=sid, agent_id="agent-ref", suite_id="support-v1", suite_version=1,
        rubric_id="r-1", rubric_version=1, run_scores=runs, visibility_tier="glass_box")


class TestRenderPdf:
    def test_renders_valid_pdf(self):
        pdf = render_pdf(_scorecard(), RUBRIC)
        assert pdf[:4] == b"%PDF" and len(pdf) > 1000

    def test_all_errored_still_renders(self):
        pdf = render_pdf(_scorecard(errored=True), RUBRIC)
        assert pdf[:4] == b"%PDF"

    def test_regression_diff_renders(self):
        pdf = render_pdf(_scorecard("sc-2"), RUBRIC, previous=_scorecard("sc-1"))
        assert pdf[:4] == b"%PDF"


CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


def _client_with_scorecard(tmp_path):
    reg = Registry(tmp_path / "a.db")
    reg.save_rubric(RUBRIC)
    reg.save_scorecard(_scorecard("sc-1"))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r", "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=reg))


class TestPdfEndpoint:
    AUTH = {"Authorization": "Bearer testtoken"}

    def test_download_pdf(self, tmp_path):
        with _client_with_scorecard(tmp_path) as c:
            r = c.get("/api/scorecards/sc-1/report.pdf", headers=self.AUTH)
            assert r.status_code == 200
            assert r.headers["content-type"] == "application/pdf"
            assert "attachment" in r.headers.get("content-disposition", "")
            assert r.content[:4] == b"%PDF"

    def test_requires_auth(self, tmp_path):
        with _client_with_scorecard(tmp_path) as c:
            assert c.get("/api/scorecards/sc-1/report.pdf").status_code == 401

    def test_unknown_scorecard_404(self, tmp_path):
        with _client_with_scorecard(tmp_path) as c:
            assert c.get("/api/scorecards/nope/report.pdf",
                         headers=self.AUTH).status_code == 404
