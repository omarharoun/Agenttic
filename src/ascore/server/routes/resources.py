"""Read-mostly registry browsing for the UI side panels: suites (with the
review file + approve action), rubrics, traces with span drill-down,
scorecards with rendered reports, managed agents, live monitor state, and
business-doc uploads."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from ascore import ops
from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.agent import DeclaredAgent
from ascore.server.auth import is_evaluator, require_operator

router = APIRouter(tags=["resources"])


def _deny_raw_traces_for_evaluator(request: Request) -> None:
    """Evaluator isolation (SPEC-2 T15.3): an independent evaluator reads only
    certified-run artifacts (dossiers, scorecards), never the owner's raw agent
    traces. Raw-trace access is hidden (404) — the trace simply doesn't exist for
    them, so no owner execution detail leaks."""
    if is_evaluator(getattr(request.state, "role", None)):
        raise HTTPException(404, "not found")


@router.get("/suites")
def list_suites(request: Request):
    return request.state.store.list_suites()


@router.get("/suites/{suite_id}")
def get_suite(suite_id: str, request: Request, version: int | None = None):
    try:
        suite, cases = request.state.reg.get_suite(suite_id, version)
    except NotFoundError:
        raise HTTPException(404, f"suite {suite_id} not found")
    return {"suite": suite.model_dump(),
            "cases": [c.model_dump() for c in cases]}


@router.get("/suites/{suite_id}/review", response_class=PlainTextResponse)
def get_review(suite_id: str, request: Request):
    review_dir = Path(request.state.cfg["paths"]["review_dir"])
    path = review_dir / f"{suite_id}.md"
    if not path.is_file():
        raise HTTPException(404, f"no review file for {suite_id}")
    return path.read_text()


@router.post("/suites/{suite_id}/approve", dependencies=[Depends(require_operator)])
def approve_suite(suite_id: str, request: Request, version: int = 1):
    try:
        request.state.reg.approve_suite(suite_id, version)
    except NotFoundError:
        raise HTTPException(404, f"suite {suite_id} v{version} not found")
    return {"approved": suite_id, "version": version}


@router.get("/rubrics")
def list_rubrics(request: Request):
    return request.state.store.list_rubrics()


@router.get("/rubrics/{rubric_id}")
def get_rubric(rubric_id: str, request: Request, version: int | None = None):
    try:
        return request.state.reg.get_rubric(rubric_id, version).model_dump()
    except NotFoundError:
        raise HTTPException(404, f"rubric {rubric_id} not found")


@router.get("/traces")
def list_traces(request: Request, agent_id: str | None = None,
                mode: str | None = None, limit: int = 50, offset: int = 0):
    _deny_raw_traces_for_evaluator(request)
    return request.state.store.list_traces(agent_id, mode, limit, offset)


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, request: Request):
    _deny_raw_traces_for_evaluator(request)
    try:
        return request.state.reg.get_trace(trace_id).model_dump(mode="json")
    except NotFoundError:
        raise HTTPException(404, f"trace {trace_id} not found")


@router.get("/scorecards")
def list_scorecards(request: Request, agent_id: str | None = None,
                    suite_id: str | None = None):
    """Results history: every persisted scorecard for the tenant (newest first).
    ``cached`` flags a result that is reusable for free on an identical re-run
    (it backs a result-cache entry)."""
    rows = request.state.store.list_scorecards(agent_id, suite_id)
    try:
        cached_ids = request.state.reg.cached_scorecard_ids()
    except Exception:  # noqa: BLE001
        cached_ids = set()
    for r in rows:
        r["cached"] = r.get("scorecard_id") in cached_ids
    return rows


@router.get("/scorecards/{scorecard_id}")
def get_scorecard(scorecard_id: str, request: Request):
    try:
        return request.state.reg.get_scorecard(scorecard_id).model_dump(mode="json")
    except NotFoundError:
        raise HTTPException(404, f"scorecard {scorecard_id} not found")


@router.get("/scorecards/{scorecard_id}/report", response_class=PlainTextResponse)
def scorecard_report(scorecard_id: str, request: Request):
    try:
        return ops.report_op(request.state.reg, scorecard_id)
    except NotFoundError:
        raise HTTPException(404, f"scorecard {scorecard_id} not found")


@router.get("/scorecards/{scorecard_id}/report.pdf")
def scorecard_report_pdf(scorecard_id: str, request: Request):
    """The same scorecard report as a polished, on-brand PDF download."""
    try:
        pdf = ops.report_pdf_op(request.state.reg, scorecard_id)
    except NotFoundError:
        raise HTTPException(404, f"scorecard {scorecard_id} not found")
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="scorecard-{scorecard_id}.pdf"'})


@router.get("/scorecards/{scorecard_id}/inspect.json")
def scorecard_inspect_log(scorecard_id: str, request: Request):
    """Export the scorecard as a UK AISI Inspect (``inspect_ai``) ``EvalLog``.

    The returned JSON validates against ``inspect_ai.log.EvalLog`` and opens in
    the Inspect viewer / re-scores in the Inspect harness — so external parties
    can re-run our evals in tooling they already trust. Auth + tenant scoped via
    ``request.state.reg`` exactly like the PDF export."""
    try:
        log = ops.inspect_log_op(request.state.reg, scorecard_id)
    except NotFoundError:
        raise HTTPException(404, f"scorecard {scorecard_id} not found")
    return JSONResponse(
        content=log,
        headers={"Content-Disposition":
                 f'attachment; filename="scorecard-{scorecard_id}.inspect.json"'})


@router.get("/agents")
def list_agents(request: Request, include_managed: bool = True):
    """Every agent the platform knows about — pre-registered in the catalog
    (declared) and/or discovered from scorecards and traces (the agent set is
    open-ended, so discovery stays descriptive), optionally enriched with
    deployed Managed Agents. Each row says where it came from and whether it's
    been scored yet."""
    agents = request.state.store.list_agents()
    by_id = {a["agent_id"]: a for a in agents}

    # fold in declared catalog entries: attach variant/connection details to
    # observed agents, and surface declared-but-never-run agents as their own
    # rows so the catalog is visible before a single run.
    for d in request.state.reg.list_declared_agents():
        existing = by_id.get(d["agent_id"])
        meta = {"declared": True, "variant": d["variant"],
                "description": d.get("description", ""), "model": d.get("model", "")}
        if existing:
            existing.update(meta)
            existing["sources"] = sorted(set(existing["sources"]) | {"declared"})
        else:
            row = {"agent_id": d["agent_id"], "sources": ["declared"],
                   "scored": False, "n_scorecards": 0, "n_traces": 0,
                   "suites": [], "last_seen": None, **meta}
            agents.append(row)
            by_id[d["agent_id"]] = row

    warning = None
    if include_managed:
        by_id = {a["agent_id"]: a for a in agents}
        try:
            import anthropic
            client = anthropic.Anthropic()
            for a in client.beta.agents.list():
                name = getattr(a, "name", "") or a.id
                existing = by_id.get(name) or by_id.get(a.id)
                if existing:
                    existing.setdefault("sources", [])
                    if "managed" not in existing["sources"]:
                        existing["sources"] = sorted(set(existing["sources"]) | {"managed"})
                    existing["managed_agent_id"] = a.id
                else:
                    agents.append({
                        "agent_id": name, "sources": ["managed"], "scored": False,
                        "n_scorecards": 0, "n_traces": 0, "suites": [],
                        "last_seen": None, "managed_agent_id": a.id,
                        "managed_version": getattr(a, "version", None)})
        except Exception as exc:  # noqa: BLE001 — never 500 a browse endpoint
            warning = f"managed agents unavailable: {type(exc).__name__}: {exc}"
    return {"agents": agents, "warning": warning}


@router.get("/agents/managed")
def managed_agents(request: Request):
    """Deployed Managed Agents (for the agent node's picker). Empty list +
    warning when no API key / network."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        return {"agents": [{"agent_id": a.id, "name": getattr(a, "name", ""),
                            "version": getattr(a, "version", None)}
                           for a in client.beta.agents.list()], "warning": None}
    except Exception as exc:  # noqa: BLE001 — browse endpoint must not 500
        return {"agents": [], "warning": f"{type(exc).__name__}: {exc}"}


@router.get("/me")
def whoami(request: Request):
    """The caller's identity: role, tenant, email (for session login), and how
    they authenticated (token | session | open)."""
    return {"role": getattr(request.state, "role", "admin"),
            "tenant": getattr(request.state, "tenant", "default"),
            "email": getattr(request.state, "user_email", None),
            "auth_method": getattr(request.state, "auth_method", "open")}


@router.get("/agents/catalog")
def list_catalog(request: Request, include_retired: bool = False):
    """The declared agent catalog — pre-registered agents (latest version each)
    with full connection details, for the run-config picker."""
    return {"agents": request.state.reg.list_declared_agents(include_retired)}


@router.post("/agents/catalog", dependencies=[Depends(require_operator)])
def register_catalog_agent(agent: DeclaredAgent, request: Request):
    """Register a new agent or store the next version of an existing one.
    Per-variant connection requirements are validated by the schema (422);
    black-box URLs are SSRF-checked here too (registration-time gate)."""
    if agent.variant == "blackbox":
        from ascore.security import UnsafeURLError, validate_blackbox_url
        try:
            validate_blackbox_url(agent.url, cfg=request.state.cfg,
                                  allow_unresolved=True)
        except UnsafeURLError as exc:
            raise HTTPException(422, f"unsafe agent url: {exc}")
    saved = request.state.reg.register_agent(agent)
    return saved.model_dump()


@router.get("/agents/catalog/{agent_id}")
def get_catalog_agent(agent_id: str, request: Request, version: int | None = None):
    try:
        return request.state.reg.get_declared_agent(agent_id, version).model_dump()
    except NotFoundError:
        raise HTTPException(404, f"declared agent {agent_id} not found")


@router.delete("/agents/catalog/{agent_id}", dependencies=[Depends(require_operator)])
def retire_catalog_agent(agent_id: str, request: Request):
    """Soft-delete: retire the agent (history is kept; re-register to revive)."""
    try:
        request.state.reg.retire_agent(agent_id)
    except NotFoundError:
        raise HTTPException(404, f"declared agent {agent_id} not found")
    return {"retired": agent_id}


@router.get("/monitor/{agent_id}")
def monitor_status(agent_id: str, request: Request):
    reg = request.state.reg
    return {"agent_id": agent_id, "reeval_requests": reg.reeval_requests(agent_id)}


@router.post("/uploads", dependencies=[Depends(require_operator)])
async def upload(request: Request, file: UploadFile):
    uploads_dir = Path(request.state.cfg.get("ui", {})
                       .get("uploads_dir", "uploads/"))
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload.txt")
    path = uploads_dir / f"{uuid.uuid4().hex[:8]}-{safe}"
    path.write_bytes(await file.read())
    return {"file_path": str(path)}


@router.post("/documents/extract", dependencies=[Depends(require_operator)])
async def extract_document(request: Request, file: UploadFile):
    """Extract plain text from an uploaded requirement document (pdf, docx, txt,
    md) so the guided Business Requirement step can be populated from a file
    instead of pasted text. In-memory only — nothing is written to disk."""
    from ascore.documents import MAX_DOCUMENT_BYTES, DocumentError, extract_text
    max_bytes = int(request.state.cfg.get("ui", {})
                    .get("max_document_mb", 0) or 0) * 1024 * 1024 \
        or MAX_DOCUMENT_BYTES
    data = await file.read()
    try:
        text = extract_text(file.filename or "", data, max_bytes=max_bytes)
    except DocumentError as exc:
        raise HTTPException(422, str(exc))
    return {"filename": file.filename or "document",
            "chars": len(text), "text": text}
