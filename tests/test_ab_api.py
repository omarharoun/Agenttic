"""A/B endpoints over HTTP: start a run, poll to completion, fetch the
comparison + Markdown + PDF, and list runs. LLM/agent calls are the same
injected fakes as the rest of the API suite (both variants share them, so this
exercises the no-significant-difference path end to end through the API)."""

import time

import pytest

from tests.test_api import client  # noqa: F401 — reuse the app+fakes fixture


def _poll_ab(client, comparison_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/ab/runs/{comparison_id}").json()
        if run["status"] in ("succeeded", "failed"):
            return run
        time.sleep(0.05)
    raise AssertionError(f"timeout; last={run}")


def _start(client, **over):
    body = {
        "suite_id": "pilot-support-triage",
        "variant_a": {"label": "A", "variant": "reference",
                      "agent_id": "router", "model": "m1"},
        "variant_b": {"label": "B", "variant": "reference",
                      "agent_id": "router", "model": "m2"},
    }
    body.update(over)
    return client.post("/api/ab/runs", json=body)


class TestABRun:
    def test_run_to_comparison(self, client):
        r = _start(client)
        assert r.status_code == 200
        cid = r.json()["comparison_id"]
        run = _poll_ab(client, cid)
        assert run["status"] == "succeeded", run
        c = run["comparison"]
        # both variants ran the same 10 pilot cases, paired
        assert c["n_paired"] == 10
        # identical behavior (shared fake agent) -> tie, no flips
        assert c["success_rate_a"] == c["success_rate_b"]
        assert c["winner"] == "tie"
        assert "No significant difference" in c["verdict"]
        assert c["flipped_cases"] == []
        # both scorecards exist and are distinct (effective ids disambiguated)
        assert c["scorecard_a_id"] != c["scorecard_b_id"]
        a = client.get(f"/api/scorecards/{c['scorecard_a_id']}").json()
        assert a["agent_id"] == "router::A"

    def test_report_and_pdf(self, client):
        cid = _start(client).json()["comparison_id"]
        _poll_ab(client, cid)
        md = client.get(f"/api/ab/runs/{cid}/report")
        assert md.status_code == 200 and "A/B Comparison" in md.text
        pdf = client.get(f"/api/ab/runs/{cid}/report.pdf")
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"
        assert pdf.headers["content-type"] == "application/pdf"

    def test_list_runs(self, client):
        cid = _start(client).json()["comparison_id"]
        _poll_ab(client, cid)
        runs = client.get("/api/ab/runs").json()
        assert any(r["comparison_id"] == cid and r["status"] == "succeeded"
                   for r in runs)

    def test_unknown_suite_404(self, client):
        r = _start(client, suite_id="does-not-exist")
        assert r.status_code == 404

    def test_invalid_variant_422(self, client):
        # blackbox variant without a url fails schema validation
        r = _start(client, variant_b={"label": "B", "variant": "blackbox",
                                       "agent_id": "x"})
        assert r.status_code == 422

    def test_report_not_ready_404(self, client):
        assert client.get("/api/ab/runs/nope/report").status_code == 404
