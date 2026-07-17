"""Multiple canonical benchmark rounds pool into ONE Agenttic Index per agent
(component means weighted by each round's sample size) — re-running the
benchmark grows the evidence behind an agent's Index instead of overwriting it.
"""

from __future__ import annotations

import json

from agenttic.ops import standard_index_op
from agenttic.registry.sqlite_store import Registry


def _run(run_id: str, *, index: float, n_cases: int,
         components: dict, suites: list[str]) -> dict:
    return {"run_id": run_id, "agent_id": "bot", "index": index,
            "n_cases": n_cases, "components": components, "suites_run": suites}


def test_single_round_passes_through(tmp_path):
    reg = Registry(tmp_path / "x.db")
    reg.save_canonical_run("r1", "bot", json.dumps(
        _run("r1", index=80.0, n_cases=10,
             components={"tool_call_accuracy": 0.8}, suites=["s1"])))
    rows = standard_index_op(reg)
    assert len(rows) == 1
    assert rows[0]["rounds"] == 1
    assert rows[0]["index"] == 80.0
    assert rows[0]["n_cases"] == 10


def test_multiple_rounds_pool_into_one_index(tmp_path):
    reg = Registry(tmp_path / "x.db")
    reg.save_canonical_run("r1", "bot", json.dumps(
        _run("r1", index=80.0, n_cases=10,
             components={"tool_call_accuracy": 0.8}, suites=["s1"])))
    reg.save_canonical_run("r2", "bot", json.dumps(
        _run("r2", index=90.0, n_cases=30,
             components={"tool_call_accuracy": 0.9}, suites=["s1", "s2"])))
    rows = standard_index_op(reg)
    assert len(rows) == 1
    row = rows[0]
    assert row["rounds"] == 2
    # evidence accumulates: 10 + 30 cases, both suites
    assert row["n_cases"] == 40
    assert row["suites_run"] == ["s1", "s2"]
    # weighted mean: (0.8·10 + 0.9·30) / 40 = 0.875 → Index 87.5 (one component)
    assert row["components"]["tool_call_accuracy"] == 0.875
    assert row["index"] == 87.5


def test_agents_stay_separate(tmp_path):
    reg = Registry(tmp_path / "x.db")
    reg.save_canonical_run("r1", "bot", json.dumps(
        _run("r1", index=80.0, n_cases=10,
             components={"tool_call_accuracy": 0.8}, suites=["s1"])))
    other = _run("r2", index=60.0, n_cases=10,
                 components={"tool_call_accuracy": 0.6}, suites=["s1"])
    other["agent_id"] = "other-bot"
    reg.save_canonical_run("r2", "other-bot", json.dumps(other))
    rows = standard_index_op(reg)
    assert [r["agent_id"] for r in rows] == ["bot", "other-bot"]  # sorted by index
    assert all(r["rounds"] == 1 for r in rows)
