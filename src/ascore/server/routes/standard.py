"""Standard (canonical) benchmarking — literature-anchored metric catalog, the
standard suites, and the per-agent Agenttic Index rollup.

These are agenttic's canonical metrics on our seed data (BFCL / tau-bench /
AgentHarm / AgentDojo *methodology*), distinct from user-generated suites.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ascore import ops
from ascore.metrics.catalog import catalog_payload, index_weights
from ascore.metrics.runner import MAX_K
from ascore.metrics.standard_suites import (
    seed_standard_suites, standard_suite_ids,
)
from ascore.server.auth import require_operator
from ascore.server.keys import KeyStore

router = APIRouter(tags=["standard"])
logger = logging.getLogger(__name__)


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


class RunBody(BaseModel):
    agent_id: str = "standard-agent"
    variant: str = "reference"
    url: str = ""
    system_prompt: str = ""
    model: str = ""
    k: int = 3


@router.post("/standard/run", dependencies=[Depends(require_operator)])
async def run_standard(body: RunBody, request: Request):
    """Run the canonical suites k times for an agent (background) and persist the
    full Agenttic Index incl. pass^k + ECE. Uses the tenant's own Anthropic key.
    Cost note: k runs cost k x the tokens."""
    cfg, reg = request.state.cfg, request.state.reg
    injected = getattr(request.state, "clients", None) or {}
    if injected:
        client = injected.get("agent")
        judge = injected.get("judge") or client
    else:
        key = KeyStore(reg.engine, cfg).get_key(getattr(request.state, "tenant", "default"))
        if not key:
            raise HTTPException(400, "Add your Anthropic API key in Settings to run "
                                     "the standard benchmarks.")
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        judge = client
    k = max(1, min(int(body.k), MAX_K))

    async def _bg():
        try:
            await ops.run_standard_op(
                cfg, reg, agent_id=body.agent_id, k=k, variant=body.variant,
                url=body.url, system_prompt=body.system_prompt, model=body.model,
                client=client, judge_client=judge)
        except Exception as exc:  # noqa: BLE001
            logger.error("standard run failed for %s: %s", body.agent_id, exc)

    asyncio.create_task(_bg())
    return {"started": True, "agent_id": body.agent_id, "k": k,
            "note": f"Running the canonical suites {k}x — k runs cost k x tokens. "
                    "Results appear on the standard leaderboard when done."}


@router.get("/standard/datasets")
def standard_datasets(request: Request):
    """Real public datasets available to ingest (BFCL now), with license +
    citation + whether each is present in this workspace."""
    from ascore.metrics.datasets import dataset_infos
    out = []
    for info in dataset_infos():
        try:
            request.state.reg.get_suite(info.suite_id)
            present = True
        except Exception:  # noqa: BLE001
            present = False
        out.append({"dataset_id": info.dataset_id, "suite_id": info.suite_id,
                    "name": info.name, "citation": info.citation,
                    "license": info.license, "source_url": info.source_url,
                    "present": present})
    return {"datasets": out}


@router.post("/standard/ingest/{dataset_id}", dependencies=[Depends(require_operator)])
def ingest_dataset(dataset_id: str, request: Request, full: bool = False):
    """Ingest a real public dataset (e.g. BFCL) into a labeled standard suite."""
    from ascore.metrics.datasets import get_adapter
    try:
        adapter = get_adapter(dataset_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    try:
        return adapter.ingest(request.state.reg, full=full)
    except Exception as exc:  # noqa: BLE001 — network/parse failure is a 502
        raise HTTPException(502, f"ingest failed: {type(exc).__name__}: {exc}")


@router.get("/standard/leaderboard")
def standard_leaderboard(request: Request):
    """Per-agent Agenttic Index across the standard metrics (components shown).
    Empty until the standard suites have been run for an agent."""
    return {"agents": ops.standard_index_op(request.state.reg),
            "metrics": catalog_payload(),
            "note": "Agenttic seed data implementing published methodology; not "
                    "the public BFCL/tau-bench/AgentHarm datasets."}
