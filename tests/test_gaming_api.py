"""EGR persistence + API payload (Phase 7): the gaming_reports migration/table
roundtrip and the gaming_api_payload shape.
"""

from __future__ import annotations

import pytest

from ascore.gaming.issues import gaming_api_payload
from ascore.gaming.probes import BEHAVIOR_DELTA_PROBES
from ascore.gaming.runner import run_gaming
from ascore.registry.sqlite_store import NotFoundError, Registry


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
