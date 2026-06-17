"""Step 7 acceptance tests (SPEC.md):
- Same suite runs against a stub HTTP server; scoring restricts to the
  black-box-applicable criteria set and the scorecard carries the tier.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ascore.adapters.blackbox_http import BlackBoxHTTPAgent
from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.scorecard import Scorecard
from ascore.schema.testcase import TestCase
from ascore.scoring.engine import applicable_criteria, score_run

RUBRIC = Rubric(rubric_id="r-bb", criteria=[
    Criterion(criterion_id="routing", description="correct queue", scorer="code",
              scale="binary", check_ref="final_output_matches_expected"),
    Criterion(criterion_id="json_ok", description="valid json", scorer="code",
              scale="binary", check_ref="valid_json_output"),
    Criterion(criterion_id="used_kb", description="kb consulted", scorer="code",
              scale="binary", check_ref="required_tool_called"),          # trajectory check
    Criterion(criterion_id="efficient", description="no waste", scorer="judge",
              scale="binary", tags=["trajectory"],
              anchors={"pass": "p", "fail": "f"}),                        # trajectory judge
])


class EchoQueueHandler(BaseHTTPRequestHandler):
    """Stub client agent: routes any ticket mentioning 'refund' to billing."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        queue = "billing" if "refund" in body.get("ticket", "") else "general"
        out = json.dumps({"output": json.dumps({"queue": queue})}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # silence test output
        pass


@pytest.fixture(scope="module")
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), EchoQueueHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/agent"
    server.shutdown()


def tc(expected):
    return TestCase(test_id="tc-1", suite_id="s-1", task_description="triage",
                    input={"ticket": "I want a refund"}, expected=expected,
                    rubric_id="r-bb")


class TestBlackBoxAdapter:
    def test_real_http_round_trip(self, stub_server):
        # localhost stub: must explicitly opt into a private target (SSRF guard)
        agent = BlackBoxHTTPAgent(agent_id="client-x", url=stub_server,
                                  allow_private_url=True)
        trace = agent.run({"ticket": "I want a refund"}, test_case_id="tc-1")
        assert trace.visibility == "black_box"
        assert json.loads(trace.final_output)["queue"] == "billing"
        assert len(trace.spans) == 1 and trace.spans[0].kind == "final_output"
        assert trace.total_latency_ms > 0

    def test_loopback_blocked_without_optin(self, stub_server):
        # without opt-in the loopback URL is refused before any network call;
        # the run is recorded as an error trace (never dialed), not an SSRF hit
        agent = BlackBoxHTTPAgent(agent_id="x", url=stub_server)  # no opt-in
        trace = agent.run({"ticket": "I want a refund"})
        assert trace.final_output.startswith("BLACKBOX_FAILURE")
        assert trace.spans[0].kind == "error"
        assert "private/reserved" in (trace.spans[0].error or "")

    def test_custom_headers_forwarded_to_endpoint(self, monkeypatch):
        # external API agents can carry auth/custom headers; they must reach
        # the HTTP transport alongside Content-Type.
        captured = {}

        def fake(url, payload, timeout, allow_private=False, headers=None):
            captured["headers"] = headers
            return {"output": "ok"}

        monkeypatch.setattr(
            "ascore.adapters.blackbox_http._http_transport", fake)
        agent = BlackBoxHTTPAgent(agent_id="x", url="http://unused",
                                  headers={"Authorization": "Bearer t"})
        agent.run({"q": 1})
        assert captured["headers"] == {"Authorization": "Bearer t"}

    def test_missing_output_field_is_data_not_crash(self):
        agent = BlackBoxHTTPAgent(agent_id="x", url="http://unused",
                                  transport=lambda p: {"wrong_key": 1})
        trace = agent.run({"q": 1})
        assert trace.final_output.startswith("BLACKBOX_FAILURE")
        assert trace.spans[0].kind == "error"

    def test_declared_per_call_cost_recorded(self):
        agent = BlackBoxHTTPAgent(agent_id="x", url="http://unused",
                                  cost_per_call_usd=0.002,
                                  transport=lambda p: {"output": "ok"})
        trace = agent.run({"q": 1})
        assert trace.total_cost_usd == 0.002

    def test_no_cost_when_call_not_made(self):
        # transport raising (caught) means no call completed -> no charge
        agent = BlackBoxHTTPAgent(agent_id="x", url="http://unused",
                                  cost_per_call_usd=0.002,
                                  transport=lambda p: (_ for _ in ()).throw(ValueError("boom")))
        trace = agent.run({"q": 1})
        assert trace.total_cost_usd == 0.0

    def test_transport_error_bubbles_to_harness(self):
        def boom(p):
            raise ConnectionError("refused")
        agent = BlackBoxHTTPAgent(agent_id="x", url="http://unused", transport=boom)
        with pytest.raises(ConnectionError):
            agent.run({"q": 1})


class TestVisibilityRestriction:
    def test_applicable_criteria_reduced_for_black_box(self):
        glass = applicable_criteria(RUBRIC, "glass_box")
        black = applicable_criteria(RUBRIC, "black_box")
        assert {c.criterion_id for c in glass} == {"routing", "json_ok",
                                                   "used_kb", "efficient"}
        assert {c.criterion_id for c in black} == {"routing", "json_ok"}

    def test_black_box_scoring_end_to_end(self, stub_server):
        agent = BlackBoxHTTPAgent(agent_id="client-x", url=stub_server,
                                  allow_private_url=True)
        trace = agent.run({"ticket": "I want a refund"}, test_case_id="tc-1")
        rs = score_run(trace, tc({"final_output": '{"queue": "billing"}'}),
                       RUBRIC, judge=None)  # no judge needed: trajectory judge excluded
        scored = {s.criterion_id for s in rs.criterion_scores}
        assert scored == {"routing", "json_ok"}
        assert rs.passed is True
        sc = Scorecard.aggregate(
            scorecard_id="sc-bb", agent_id="client-x", suite_id="s-1",
            suite_version=1, rubric_id="r-bb", rubric_version=1,
            run_scores=[rs], visibility_tier="black_box",
        )
        assert sc.visibility_tier == "black_box"

    def test_all_trajectory_rubric_rejected_for_black_box(self):
        r = Rubric(rubric_id="r-t", criteria=[RUBRIC.criteria[2]])
        with pytest.raises(ValueError, match="no criteria applicable"):
            applicable_criteria(r, "black_box")
