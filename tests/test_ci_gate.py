"""CI safety-gate logic — grade-vs-threshold gating must never silently regress.

These tests exercise gate.py's decision function directly (no network, no
certify run) so the merge-blocking behavior is pinned.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_GATE = Path(__file__).resolve().parents[1] / ".github/actions/agent-safety/gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("agenttic_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _passes(grade: str, threshold: str) -> bool:
    g = _load()
    return threshold == "NONE" or g.GRADE_ORDER[grade] >= g.GRADE_ORDER[threshold]


def test_grade_order_is_total_and_ranked():
    g = _load()
    assert g.GRADE_ORDER["A"] > g.GRADE_ORDER["B"] > g.GRADE_ORDER["C"] > g.GRADE_ORDER["NONE"]


def test_a_passes_every_real_threshold():
    for t in ("A", "B", "C"):
        assert _passes("A", t) is True


def test_c_fails_b_and_a_but_passes_c():
    assert _passes("C", "A") is False
    assert _passes("C", "B") is False
    assert _passes("C", "C") is True


def test_b_gates_correctly():
    assert _passes("B", "A") is False
    assert _passes("B", "B") is True
    assert _passes("B", "C") is True


def test_none_threshold_is_report_only():
    for grade in ("A", "B", "C"):
        assert _passes(grade, "NONE") is True


def test_summary_surfaces_not_assessed_domains():
    g = _load()
    dossier = {
        "agent_id": "x", "profile_id": "p", "profile_version": 1,
        "dossier_id": "d", "content_sha256": "abc123",
        "tier_decision": {"tier": "C", "caps_applied": ["provisional_judge"]},
        "coverage": [
            {"domain": "harm_refusal", "status": "assessed_seed"},
            {"domain": "cbrn_proxy", "status": "not_assessed"},
        ],
    }
    md = g.summarize(dossier, "C", "B", passed=False)
    # honest reporting: the grade AND the unassessed domain are both visible
    assert "Grade **C**" in md
    assert "NOT ASSESSED" in md
    assert "cbrn_proxy" in md
    assert "BELOW THRESHOLD" in md
