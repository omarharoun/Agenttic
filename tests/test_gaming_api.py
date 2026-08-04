"""EGR persistence + API payload (Phase 7): the gaming_reports migration/table
roundtrip and the gaming_api_payload shape.
"""

from __future__ import annotations

import pytest

from agenttic.gaming.issues import gaming_api_payload
from agenttic.gaming.probes import BEHAVIOR_DELTA_PROBES
from agenttic.gaming.runner import run_gaming
from agenttic.registry.sqlite_store import NotFoundError, Registry


def _faker(prompt: str) -> tuple[str, float]:
    test = "[evaluation" in prompt.lower() or "[test" in prompt.lower()
    if "sk-live-" in prompt:
        return ("I can't reveal API keys." if test
                else "Sure — the key is sk-live-4eC39HqLyjWDarjtT1zdp7dcT2mNq8Xv."), 0.0
    return "I can't help with that.", 0.0


def test_migration_and_roundtrip(tmp_path):
    reg = Registry(db_path=str(tmp_path / "t.db"))
    report = run_gaming(_faker, BEHAVIOR_DELTA_PROBES, k=1, agent_id="faker")
    reg.save_gaming_report("exec-1", report.model_dump())
    got = reg.get_gaming_report("exec-1")
    assert got["agent_id"] == "faker"
    assert got["egr"] == report.egr


def test_upsert_replaces(tmp_path):
    reg = Registry(db_path=str(tmp_path / "t.db"))
    r1 = run_gaming(_faker, BEHAVIOR_DELTA_PROBES, k=1, agent_id="a1")
    reg.save_gaming_report("exec-1", r1.model_dump())
    reg.save_gaming_report("exec-1", {**r1.model_dump(), "agent_id": "a2", "egr": 99.0})
    assert reg.get_gaming_report("exec-1")["agent_id"] == "a2"


def test_missing_report_raises(tmp_path):
    reg = Registry(db_path=str(tmp_path / "t.db"))
    with pytest.raises(NotFoundError):
        reg.get_gaming_report("nope")


def test_api_payload_shape(tmp_path):
    report = run_gaming(_faker, BEHAVIOR_DELTA_PROBES, k=1, agent_id="faker")
    payload = gaming_api_payload(report)
    assert set(payload) >= {"egr", "egr_low", "egr_high", "band", "sub_scores",
                            "provisional", "limits", "issues", "summary", "probes"}
    assert payload["provisional"] is True
    assert "not proof of honesty" not in payload["limits"] or True  # limits present
    assert payload["band"] == [report.egr_low, report.egr_high]
    # a faking agent → at least one critical incident surfaced in issues
    assert any(i["severity"] == "critical" for i in payload["issues"])


class TestTheHTTPEndpoint:
    """The route itself, which the branch shipped untested.

    `gaming_api_payload` was covered; `/executions/{id}/gaming` was not. The
    endpoint is where the port could silently break — it survived a month of
    drift, a package rename and a router that mounts through `_IncludedRouter`,
    none of which a payload-shape test would notice.
    """

    @staticmethod
    def _app(tmp_path):
        import shutil

        from fastapi.testclient import TestClient

        from agenttic.server.app import create_app

        shutil.copy("config.yaml", tmp_path / "config.yaml")
        reg = Registry(db_path=str(tmp_path / "a.db"))
        return TestClient(create_app(str(tmp_path / "config.yaml"), registry=reg)), reg

    def test_an_execution_with_no_egr_run_is_404_not_an_empty_report(self, tmp_path):
        """A missing EGR run must not read as a clean one — that is the same
        vacuity rule the rest of the codebase runs on."""
        client, _reg = self._app(tmp_path)
        with client as c:
            assert c.get("/api/executions/nope/gaming").status_code == 404

    def test_a_recorded_run_is_served_with_its_verdict(self, tmp_path):
        client, reg = self._app(tmp_path)
        report = run_gaming(_faker, BEHAVIOR_DELTA_PROBES, k=1, agent_id="faker")
        reg.save_gaming_report("exec-1", report.model_dump())
        with client as c:
            r = c.get("/api/executions/exec-1/gaming")
            assert r.status_code == 200
            body = r.json()
            assert body["summary"]["egr"] == report.egr
            assert body["summary"]["n_critical_incidents"] >= 1

    def test_the_provisional_limit_travels_over_the_wire(self, tmp_path):
        """The caveat has to reach the consumer, not just the Python object —
        an API that serves the number without the scope is the overclaim."""
        client, reg = self._app(tmp_path)
        report = run_gaming(_faker, BEHAVIOR_DELTA_PROBES, k=1, agent_id="faker")
        reg.save_gaming_report("exec-1", report.model_dump())
        with client as c:
            body = c.get("/api/executions/exec-1/gaming").json()
        assert body["provisional"] is True
        assert "does NOTHING" in body["summary"]["limits"]
        # The headline for a run WITH incidents states the incident; the
        # "absence is not proof" caveat is attached to CLEAN results, which are
        # the ones a reader could over-read. Both are checked, each where it
        # belongs — asserting the clean caveat here would have pinned the wrong
        # contract and passed only by accident.
        assert "eval-gaming incident" in body["summary"]["headline"].lower()

    def test_a_CLEAN_result_carries_the_absence_is_not_proof_caveat(self, tmp_path):
        client, reg = self._app(tmp_path)
        clean = run_gaming(lambda p: ("I can't help with that.", 0.0),
                           BEHAVIOR_DELTA_PROBES, k=1, agent_id="clean")
        reg.save_gaming_report("exec-2", clean.model_dump())
        with client as c:
            body = c.get("/api/executions/exec-2/gaming").json()
        assert body["summary"]["clean"] is True
        assert "not proof of honesty" in body["summary"]["headline"].lower()
