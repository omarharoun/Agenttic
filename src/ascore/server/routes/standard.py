"""Standard (canonical) benchmarking — literature-anchored metric catalog, the
standard suites, and the per-agent Agenttic Index rollup.

These are agenttic's canonical metrics on our seed data (BFCL / tau-bench /
AgentHarm / AgentDojo *methodology*), distinct from user-generated suites.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ascore import ops
from ascore.metrics.catalog import catalog_payload, index_weights
from ascore.metrics.standard_suites import (
    seed_standard_suites, standard_suite_ids,
)
from ascore.server.auth import require_operator

router = APIRouter(tags=["standard"])


@router.get("/standard/metrics")
def standard_metrics(request: Request):
    """The canonical metric catalog: names, the literature methodology each
    implements, categories, and the Agenttic Index weighting."""
    return {"metrics": catalog_payload(), "index_weights": index_weights()}


@router.get("/standard/suites")
def standard_suites(request: Request):
    seeded = []
    for sid in standard_suite_ids():
        try:
            request.state.reg.get_suite(sid)
            seeded.append(sid)
        except Exception:  # noqa: BLE001
            pass
    return {"suite_ids": standard_suite_ids(), "seeded": seeded}


@router.post("/standard/seed", dependencies=[Depends(require_operator)])
def seed(request: Request):
    """Install the canonical standard suites into this workspace (idempotent)."""
    return {"seeded": seed_standard_suites(request.state.reg)}


@router.get("/standard/leaderboard")
def standard_leaderboard(request: Request):
    """Per-agent Agenttic Index across the standard metrics (components shown).
    Empty until the standard suites have been run for an agent."""
    return {"agents": ops.standard_index_op(request.state.reg),
            "metrics": catalog_payload(),
            "note": "Agenttic seed data implementing published methodology; not "
                    "the public BFCL/tau-bench/AgentHarm datasets."}
