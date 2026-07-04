"""Execution lifecycle: start, inspect, SSE event stream, approve the human
gate, cancel, resume. The SSE stream replays persisted events after the
client's last seen seq (``?after=`` or the EventSource Last-Event-ID header),
then follows live."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ascore.registry.sqlite_store import NotFoundError
from ascore.server.auth import require_operator
from ascore.server.executor import WorkflowValidationError
from ascore.server.keys import tenant_run_clients as _run_clients

router = APIRouter(tags=["executions"])


@router.post("/workflows/{workflow_id}/executions",
             dependencies=[Depends(require_operator)])
async def start_execution(workflow_id: str, request: Request,
                          force: bool = False):
    # async: the manager calls asyncio.create_task, which needs the loop
    # ?force=true bypasses the result cache and re-runs fresh.
    state = request.state
    try:
        wf = state.store.get_workflow(workflow_id)
    except NotFoundError:
        raise HTTPException(404, f"workflow {workflow_id} not found")
    clients = _run_clients(request)  # tenant key (or None for injected clients)
    try:
        execution_id = state.manager.start(wf, clients=clients, force=force)
    except WorkflowValidationError as exc:
        raise HTTPException(422, detail={"problems": exc.problems})
    return {"execution_id": execution_id}


@router.get("/executions")
def list_executions(request: Request, workflow_id: str | None = None):
    return request.state.store.list_executions(workflow_id)


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, request: Request):
    try:
        return request.state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")


def _assemble_results(state, ex) -> tuple[list, list, set]:
    """Join an execution's persisted node outputs into render-ready scorecards +
    per-case rows, and collect the (rubric_id, version) pairs the run scored
    against. Shared by the results and issues endpoints."""
    scorecards, cases_out, rubric_refs = [], [], set()
    for node_id, ports in (ex.get("node_outputs") or {}).items():
        for payload in ports.values():
            if not isinstance(payload, dict):
                continue
            if "scorecard_id" in payload:  # scorecard node output
                try:
                    sc = state.reg.get_scorecard(payload["scorecard_id"])
                except NotFoundError:
                    continue
                rubric_refs.add((sc.rubric_id, sc.rubric_version))
                cached = bool(payload.get("cached"))
                scorecards.append({
                    "node_id": node_id, "scorecard_id": sc.scorecard_id,
                    "agent_id": sc.agent_id, "suite_id": sc.suite_id,
                    "suite_version": sc.suite_version,
                    "task_success_rate": sc.task_success_rate,
                    # sample size + Wilson 95% interval for an honest headline
                    "n_scored": sc.n_scored,
                    "n_passed": sc.n_passed,
                    "success_wilson_low": sc.success_wilson_low,
                    "success_wilson_high": sc.success_wilson_high,
                    # on a cache hit this run made no new calls — its run cost is $0
                    "mean_cost_usd": 0.0 if cached else sc.mean_cost_usd,
                    "total_cost_usd": 0.0 if cached else sc.total_cost_usd,
                    "total_scoring_cost_usd": 0.0 if cached else sc.total_scoring_cost_usd,
                    "cached": cached,
                    "p95_latency_ms": sc.p95_latency_ms,
                    "per_criterion_means": sc.per_criterion_means,
                    "errored_test_ids": sc.errored_test_ids,
                    "visibility_tier": sc.visibility_tier,
                })
            if "run_scores" in payload:  # score node output
                _, suite_cases = state.reg.get_suite(
                    payload["suite_id"], payload["suite_version"])
                expected_by_id = {c.test_id: c.expected for c in suite_cases}
                for rs in payload["run_scores"]:
                    try:
                        prediction = state.reg.get_trace(
                            rs["trace_id"]).final_output
                    except NotFoundError:
                        prediction = ""
                    cases_out.append({
                        "node_id": node_id,
                        "test_id": rs["test_id"],
                        "passed": rs["passed"],
                        "scoring_error": rs.get("scoring_error"),
                        "prediction": prediction,
                        "expected": expected_by_id.get(rs["test_id"]),
                        "cost_usd": rs.get("cost_usd"),
                        "scoring_cost_usd": rs.get("scoring_cost_usd"),
                        "steps": rs.get("steps"),
                        "latency_ms": rs.get("latency_ms"),
                        "criteria": [{
                            "criterion_id": cs["criterion_id"],
                            "score": cs["score"], "scorer": cs["scorer"],
                            "calibrated": cs.get("calibrated", True),
                            "rationale": cs.get("judge_rationale"),
                        } for cs in rs["criterion_scores"]],
                    })
    cases_out.sort(key=lambda r: (r["node_id"], r["test_id"]))
    return scorecards, cases_out, rubric_refs


@router.get("/executions/{execution_id}/results")
def execution_results(execution_id: str, request: Request):
    """Joined, render-ready results for an execution: scorecard summaries
    plus one row per test case — the agent's actual output (its prediction),
    the expected value, per-criterion scores, and judge rationales."""
    state = request.state
    try:
        ex = state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")
    scorecards, cases_out, _ = _assemble_results(state, ex)
    return {"status": ex["status"], "scorecards": scorecards,
            "cases": cases_out}


@router.get("/executions/{execution_id}/issues")
def execution_issues(execution_id: str, request: Request):
    """The Issues report — this execution's REAL failures, ranked worst-first,
    each with a plain-language why, the failing cases as evidence, and which Fix
    capability addresses it. Derived entirely from computed scores (no fabricated
    findings); an all-passing run honestly reports zero issues."""
    from ascore.issues import build_issues

    state = request.state
    try:
        ex = state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")
    scorecards, cases_out, rubric_refs = _assemble_results(state, ex)

    # per-criterion metadata (description/scorer/scale/check_ref/tags) from the
    # run's rubrics — powers category inference and the human-readable "why".
    criteria_meta: dict[str, dict] = {}
    for rid, ver in rubric_refs:
        try:
            rubric = state.reg.get_rubric(rid, ver)
        except NotFoundError:
            continue
        for c in rubric.criteria:
            criteria_meta.setdefault(c.criterion_id, {
                "description": c.description, "scorer": c.scorer,
                "scale": c.scale, "check_ref": c.check_ref, "tags": c.tags})

    report = build_issues(scorecards=scorecards, cases=cases_out,
                          criteria_meta=criteria_meta)
    return {"status": ex["status"], **report}


@router.get("/executions/{execution_id}/gaming")
def execution_gaming(execution_id: str, request: Request):
    """Evaluation-Gaming Resistance (EGR) for this execution — the PROVISIONAL
    headline band, the four sub-scores, and any eval-gaming incidents with
    side-by-side test-vs-deployment transcripts. 404 if the execution recorded no
    EGR run. HONESTY: a high EGR is evidence of the ABSENCE OF DETECTABLE gaming,
    not proof of honesty (see docs/GAMING_SPEC.md §4.3)."""
    from ascore.gaming.issues import gaming_api_payload
    from ascore.gaming.schema import GamingReport

    state = request.state
    try:
        report_dict = state.reg.get_gaming_report(execution_id)
    except NotFoundError:
        raise HTTPException(
            404, f"no eval-gaming (EGR) run recorded for execution {execution_id}")
    return gaming_api_payload(GamingReport.model_validate(report_dict))


@router.get("/executions/{execution_id}/events")
async def stream_events(execution_id: str, request: Request, after: int = 0):
    state = request.state
    try:
        state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")
    last_id = request.headers.get("last-event-id")
    if last_id and last_id.isdigit():
        after = max(after, int(last_id))

    async def gen():
        async for evt in state.bus.subscribe(execution_id, after=after):
            yield {"id": str(evt["seq"]), "event": evt["type"],
                   "data": json.dumps({"node_id": evt["node_id"],
                                       "data": evt["data"],
                                       "seq": evt["seq"]})}
        yield {"event": "stream_end", "data": "{}"}

    return EventSourceResponse(gen())


@router.post("/executions/{execution_id}/approve",
             dependencies=[Depends(require_operator)])
async def approve_execution(execution_id: str, request: Request):
    """Approve the suite a gated execution is waiting on, then release the
    gate (in-process) or resume (after a server restart)."""
    state = request.state
    try:
        ex = state.store.get_execution(execution_id)
    except NotFoundError:
        raise HTTPException(404, f"execution {execution_id} not found")
    if ex["status"] != "waiting_approval":
        raise HTTPException(409, f"execution is {ex['status']}, not waiting")
    waiting = [e for e in state.store.events_after(execution_id)
               if e["type"] == "node_waiting"]
    if not waiting:
        raise HTTPException(409, "no node_waiting event recorded")
    info = waiting[-1]["data"]
    state.reg.approve_suite(info["suite_id"], info["version"])
    if not state.manager.approve_gate(execution_id):
        state.manager.resume(execution_id, clients=_run_clients(request))
    return {"approved": {"suite_id": info["suite_id"],
                         "version": info["version"]}}


@router.post("/executions/{execution_id}/cancel",
             dependencies=[Depends(require_operator)])
async def cancel_execution(execution_id: str, request: Request):
    await request.state.manager.cancel(execution_id)
    return {"cancelled": execution_id}


@router.post("/executions/{execution_id}/resume",
             dependencies=[Depends(require_operator)])
async def resume_execution(execution_id: str, request: Request):
    try:
        request.state.manager.resume(execution_id, clients=_run_clients(request))
    except (NotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    return {"resumed": execution_id}
