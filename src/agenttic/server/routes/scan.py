"""Public "Scan my agent" convenience endpoint — the consumer on-ramp.

A normal user points us at their agent (an HTTP endpoint, with an optional auth
header) or picks the built-in demo agent, and gets back a signed A–F safety
grade in a scan report (not a certificate — ~14 probes cannot close coverage).
This route is a THIN orchestrator over the existing engine:

    POST /api/scan            start a scan (background); returns a scan_id
    GET  /api/scan/{scan_id}  poll live progress + the graded result + cert
    GET  /api/scan/preview    what a scan will do (dimensions, key/cost) before running

The heavy lifting is ``agenttic.scan.run_safety_scan`` (build adapter → run + score
the Safety Battery → grade) and ``server.certifications.issue_scan_report`` (the
signed, tamper-evident **scan report** — a screen is not a certificate, so this
route never issues one). We add no scoring here.

Honesty / cost:
* A scan against the user's OWN endpoint (``target=endpoint``) spends NO Anthropic
  tokens and needs NO key — the agent runs on the user's infra and the safety
  battery is scored by deterministic code checks.
* The demo agent (``target=demo``) runs the built-in reference agent with the
  tenant's OWN Anthropic key (BYO-key); we surface a clear "add your key" path
  and never fall back to a shared key.

Progress is exposed by polling (simple + robust for a short consumer scan). Jobs
live in-process; the durable artifacts (scorecard + signed scan report) are
persisted by the engine, so a restart loses only the transient progress bar.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agenttic import certification as cert
from agenttic import ops, scan
from agenttic.metrics.safety_battery import BATTERY_DIMENSIONS, DIMENSION_BY_CRITERION
from agenttic.registry.sqlite_store import NotFoundError
from agenttic.server.abuse import guard_cost_endpoint, guard_public_demo
from agenttic.server.auth import require_operator
from agenttic.server.certifications import issue_scan_report
from agenttic.server.keys import NO_KEY_MSG, KeyStore

router = APIRouter(tags=["scan"])
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# In-process scan-job store (transient progress only; artifacts are persisted).
# --------------------------------------------------------------------------- #


@dataclass
class ScanJob:
    scan_id: str
    tenant: str
    target: str
    agent_name: str
    status: str = "running"          # running | done | error
    phase: str = "Starting the scan…"
    progress: float = 0.0            # 0..1
    n_cases: int = 0
    cases_done: int = 0
    # per-dimension live checklist (pending → pass/fail once graded)
    checks: list = field(default_factory=list)
    result: dict | None = None       # the graded scan result (scan.run_safety_scan)
    certificate: dict | None = None  # the issued scan-report view (artifact=scan_report)
    cert_note: str | None = None
    error: str | None = None

    def public(self) -> dict:
        d = asdict(self)
        d.pop("tenant", None)
        return d


_JOBS: dict[str, ScanJob] = {}
_LOCK = threading.Lock()

#: map a battery test_id to its dimension's plain label (for the live phase line)
_CRIT_FOR_TID = (
    ("refusal", "harmful_action_refused"),
    ("injection", "injection_robust"),
    ("secret", "no_secret_leak"),
    ("toolsafe", "tool_misuse_safety"),
)
_LABEL_FOR_CRIT = {d.criterion_id: d.plain_label for d in BATTERY_DIMENSIONS}


def _dim_for_test(test_id: str) -> str | None:
    for token, crit in _CRIT_FOR_TID:
        if token in test_id:
            return crit
    return None


def preview_dimensions() -> list[dict]:
    """The dimensions a scan measures, as plain-language rows. Shared by the
    scan-preview routes and the Copilot ``preview_scan`` tool so both describe a
    scan the same way."""
    return [{"criterion_id": d.criterion_id, "label": d.plain_label,
             "critical": d.criterion_id in
             {BATTERY_DIMENSIONS[0].criterion_id, BATTERY_DIMENSIONS[1].criterion_id}}
            for d in BATTERY_DIMENSIONS]


def _initial_checks() -> list[dict]:
    return [{"criterion_id": d.criterion_id, "label": d.plain_label,
             "status": "pending", "passed": None, "detail": "",
             "critical": d.criterion_id in
             {BATTERY_DIMENSIONS[0].criterion_id, BATTERY_DIMENSIONS[1].criterion_id}}
            for d in BATTERY_DIMENSIONS]


# --------------------------------------------------------------------------- #
# Request models.
# --------------------------------------------------------------------------- #


class ScanBody(BaseModel):
    # "endpoint" (raw BYO URL) | "demo" (reference agent) | "connection" (the
    # saved "Connect your agent" config — presets, mapping, consent gate).
    target: str = "endpoint"
    url: str = ""                     # required for target=endpoint
    header_name: str = ""             # optional single auth header, e.g. "Authorization"
    header_value: str = ""            # e.g. "Bearer sk-..."
    agent_name: str = ""              # display name on the scan report
    expires_days: int = cert.DEFAULT_EXPIRY_DAYS


def _build_scan_adapter(request: Request, body: ScanBody):
    """Build the agent adapter + the scoring judge client for a scan.

    Returns ``(adapter, judge_client, agent_id)``. Raises HTTPException(400) with
    a friendly message when a demo scan has no Anthropic key configured."""
    cfg, reg = request.state.cfg, request.state.reg
    injected = getattr(request.state, "clients", None) or {}
    target = (body.target or "endpoint").lower()

    if target == "connection":
        # Scan the saved "Connect your agent" config. NO Anthropic key needed.
        # The consent gate is mandatory: the user must have confirmed they
        # own/are-authorized-to-test the agent before we send it any traffic.
        from agenttic.connect import build_connection_adapter
        from agenttic.server.connections import ConnectionStore
        tenant = getattr(request.state, "tenant", "default")
        conn = ConnectionStore(reg.engine, cfg).get(tenant)
        if conn is None:
            raise HTTPException(400, "Connect your agent first, then run the scan.")
        if not conn.consent:
            raise HTTPException(
                403, "Confirm you own or are authorized to test this agent before "
                     "scanning (the authorization checkbox in the connect step).")
        agent_id = body.agent_name.strip() or conn.agent_name or "your-agent"
        adapter = build_connection_adapter(cfg, conn, agent_id=agent_id)
        return adapter, None, agent_id

    if target == "demo":
        agent_id = "agenttic-demo-agent"
        if injected:
            client = injected.get("agent")
        else:
            key = KeyStore(reg.engine, cfg).get_key(
                getattr(request.state, "tenant", "default"))
            if not key:
                raise HTTPException(400, NO_KEY_MSG)
            import anthropic
            client = anthropic.Anthropic(api_key=key)
        adapter = ops.build_adapter(cfg, variant="reference", agent_id=agent_id,
                                    client=client)
        # judge stays code-only (battery is deterministic); reuse the same client
        return adapter, client, agent_id

    # target == endpoint (black-box): NO Anthropic key needed at all.
    if not body.url.strip():
        raise HTTPException(422, "Paste your agent's API endpoint URL to scan it.")
    headers = None
    if body.header_name.strip() and body.header_value.strip():
        headers = {body.header_name.strip(): body.header_value.strip()}
    agent_id = body.agent_name.strip() or "your-agent"
    try:
        adapter = ops.build_adapter(cfg, variant="blackbox", agent_id=agent_id,
                                    url=body.url.strip(), headers=headers)
    except ops.AgentConfigError as exc:
        raise HTTPException(400, str(exc))
    return adapter, None, agent_id


# --------------------------------------------------------------------------- #
# Routes.
# --------------------------------------------------------------------------- #


@router.get("/scan/preview")
def scan_preview(request: Request):
    """What a scan will measure + what it costs, so the UI can set expectations
    and surface the BYO-key path before the user commits."""
    cfg, reg = request.state.cfg, request.state.reg
    injected = getattr(request.state, "clients", None) or {}
    key_set = bool(injected) or bool(KeyStore(reg.engine, cfg).get_key(
        getattr(request.state, "tenant", "default")))
    return {
        "dimensions": preview_dimensions(),
        "endpoint": {
            "needs_key": False,
            "note": "We send the safety probes to your endpoint and grade the "
                    "answers. No Anthropic key and no Agenttic spend — your agent "
                    "runs on your own infrastructure.",
        },
        "demo": {
            "needs_key": False,
            "key_set": key_set,
            "note": "Runs the built-in demo agent on the server's own key — no "
                    "account or API key needed. A live run with fresh results "
                    "every time.",
        },
    }


def _start_scan_job(cfg, reg, global_engine, *, tenant: str, target: str,
                    agent_name: str, adapter, judge_client,
                    expires_days: int, issue_cert: bool = True,
                    no_cert_note: str | None = None, loop=None) -> str:
    """Create a ScanJob, kick off the background run, and return its scan_id.
    Shared by the authed ``POST /scan`` and the anonymous public demo route.
    ``issue_cert=False`` grades without minting a certificate (the anonymous
    demo — so demo runs never enter the public certified directory).

    Scheduling: on the request event loop (the async routes) we ``create_task``.
    When called from OFF the loop (e.g. the Copilot tool runs in a worker thread),
    pass ``loop`` — the app's stored event loop — and we schedule with
    ``run_coroutine_threadsafe`` instead, so background scans work from either
    surface without changing route behavior."""
    scan_id = "scan_" + uuid.uuid4().hex[:16]
    job = ScanJob(scan_id=scan_id, tenant=tenant, target=target,
                  agent_name=agent_name,
                  checks=_initial_checks())
    with _LOCK:
        _JOBS[scan_id] = job

    def _on_progress(etype: str, data: dict) -> None:
        total = int(data.get("total") or 0)
        with _LOCK:
            if total and not job.n_cases:
                job.n_cases = total
            if etype in ("case_finished", "case_resumed", "budget_exceeded"):
                job.cases_done += 1
                crit = _dim_for_test(str(data.get("test_id", "")))
                label = _LABEL_FOR_CRIT.get(crit, "your agent")
                job.phase = f"Probing: {label}"
            elif etype == "case_scored" or etype == "case_error":
                job.phase = "Scoring the results…"
            # progress: execution is the bulk for a black-box scan; scoring is fast
            if job.n_cases:
                exec_frac = min(1.0, job.cases_done / job.n_cases)
                job.progress = round(0.05 + 0.8 * exec_frac, 3)

    async def _run() -> None:
        try:
            result = await scan.run_safety_scan(
                cfg, reg, adapter=adapter, judge_client=judge_client,
                on_progress=_on_progress)
            with _LOCK:
                job.progress = 0.92
                job.phase = "Grading…"
                job.result = result
                # resolve the live checklist from the graded dimensions
                by_crit = {d["criterion_id"]: d for d in result.get("dimensions", [])}
                for chk in job.checks:
                    d = by_crit.get(chk["criterion_id"])
                    if d:
                        chk.update(status=d["status"], passed=d["passed"],
                                   detail=d["detail"], percent=d["percent"])
            # issue a signed certificate from the completed scorecard
            # Issue a signed SCAN REPORT, not a certificate. ~14 probes cannot
            # close a coverage target and were never meant to; calling the result
            # a certificate would be the overclaim the signing gate exists to
            # prevent. The report is still signed — integrity, not endorsement —
            # and it states on its face that it is not a certificate.
            #
            # `issue_cert=False` withholds even that: the anonymous demo grades
            # the agent and mints nothing, carrying `no_cert_note` instead. The
            # gate and the artifact are separate decisions — what gets issued,
            # and whether anything is issued at all.
            if issue_cert:
                try:
                    view = issue_scan_report(
                        global_engine=global_engine, cfg=cfg, reg=reg,
                        tenant=tenant, scorecard_id=result["scorecard_id"],
                        expires_days=expires_days)
                    with _LOCK:
                        job.certificate = view
                except cert.CertificationError as exc:
                    with _LOCK:
                        job.cert_note = (
                            "We scanned your agent but couldn't issue a scan "
                            f"report: {exc}")
                except Exception as exc:  # noqa: BLE001 — cert is best-effort
                    logger.error("scan %s cert issue failed: %s", scan_id, exc)
                    with _LOCK:
                        job.cert_note = ("We graded your agent; scan-report "
                                         "issuance is temporarily unavailable.")
            elif no_cert_note:
                with _LOCK:
                    job.cert_note = no_cert_note
            # Meter the tenant's model spend for this scan as a credit debit
            # (best-effort). Black-box/endpoint scans report $0 (they run on the
            # user's own infra) and so debit nothing.
            scan_cost = float(result.get("cost_usd") or 0.0)
            if scan_cost > 0:
                try:
                    from agenttic.billing import service as billing_service
                    billing_service.meter_cost(
                        reg.engine, tenant, "scan", scan_cost, cfg=cfg,
                        ref=scan_id)
                except Exception:  # noqa: BLE001 — metering must not fail the scan
                    pass
            with _LOCK:
                job.progress = 1.0
                job.phase = "Done"
                job.status = "done"
        except Exception as exc:  # noqa: BLE001 — surface a friendly error
            logger.error("scan %s failed: %s", scan_id, exc)
            with _LOCK:
                job.status = "error"
                job.error = f"{type(exc).__name__}: {exc}"
                job.phase = "Scan failed"

    try:
        asyncio.get_running_loop()
        on_loop = True
    except RuntimeError:
        on_loop = False
    if on_loop:
        asyncio.create_task(_run())
    elif loop is not None:
        asyncio.run_coroutine_threadsafe(_run(), loop)
    else:
        raise RuntimeError(
            "no running event loop and no loop provided to schedule the scan")
    return scan_id


@router.post("/scan", dependencies=[Depends(require_operator)])
async def start_scan(body: ScanBody, request: Request):
    """Start a safety scan (runs in the background). Returns a ``scan_id`` to poll
    at ``GET /api/scan/{scan_id}``."""
    # Abuse ceiling: bound how fast one IP / tenant / the whole server can start
    # scans (a demo scan spends a metered key; every scan spins up a job).
    guard_cost_endpoint(request, "scan")
    cfg, reg = request.state.cfg, request.state.reg
    tenant = getattr(request.state, "tenant", "default")
    # A demo scan spends the tenant's metered model budget, so gate it on credits
    # (endpoint/connection scans run on the user's own infra → no gate).
    if (body.target or "endpoint").lower() == "demo":
        from agenttic.billing import service as billing_service
        try:
            billing_service.ensure_credits(reg.engine, tenant, cfg)
        except billing_service.OutOfCreditsError as exc:
            raise HTTPException(402, str(exc))
    adapter, judge_client, agent_id = _build_scan_adapter(request, body)
    # Gentle traffic against a user's live agent: force sequential (1-in-flight)
    # requests. Per-request timeout + rate limit are set on the connection adapter.
    if (body.target or "").lower() == "connection":
        from agenttic.connect import gentle_scan_cfg
        cfg = gentle_scan_cfg(cfg)

    scan_id = _start_scan_job(
        cfg, reg, request.app.state.reg.engine, tenant=tenant,
        target=body.target, agent_name=body.agent_name.strip() or agent_id,
        adapter=adapter, judge_client=judge_client,
        expires_days=body.expires_days)
    return {"scan_id": scan_id, "target": body.target,
            "n_dimensions": len(BATTERY_DIMENSIONS)}


def job_status_for_tenant(scan_id: str, tenant: str) -> dict:
    """Public snapshot of a scan job, scoped to one tenant. Returns the same
    ``job.public()`` shape the poll routes return, or raises 404 if the job
    doesn't exist / belongs to another tenant. Shared by the authed/public poll
    routes and the Copilot ``get_scan_status`` tool (which forces the demo
    tenant on the public surface so it can only see demo jobs)."""
    with _LOCK:
        job = _JOBS.get(scan_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(404, f"scan {scan_id} not found")
        return job.public()


@router.get("/scan/{scan_id}")
def scan_status(scan_id: str, request: Request):
    """Poll a scan: live phase/progress, the per-dimension checklist, and (once
    done) the grade + signed certificate. 404 if it isn't this tenant's scan."""
    tenant = getattr(request.state, "tenant", "default")
    return job_status_for_tenant(scan_id, tenant)


# --------------------------------------------------------------------------- #
# Per-probe findings — the Safety Scan Report behind a completed scan.
#
# The durable evidence is the persisted scorecard (+ its traces + the suite's
# cases); the scan job only carries the scorecard_id pointer. We join those
# three stores into one plain-language, per-probe findings document. Everything
# here reads existing artifacts — no new scoring, no fabrication: when the
# evidence isn't there (job still running, restarted server lost the pointer,
# a trace is missing) the response says so honestly.
# --------------------------------------------------------------------------- #

#: max characters of the agent's answer / tool-call excerpt we echo back
_EXCERPT_CHARS = 300


def _excerpt(value) -> str:
    text = str(value or "").strip()
    if len(text) <= _EXCERPT_CHARS:
        return text
    return text[: _EXCERPT_CHARS - 1].rstrip() + "…"


def _job_findings_snapshot(scan_id: str, tenant: str) -> tuple:
    """Look up a job under the lock and snapshot what the assembler needs.
    Raises 404 when the job doesn't exist or belongs to another tenant."""
    with _LOCK:
        job = _JOBS.get(scan_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(404, f"scan {scan_id} not found")
        result = dict(job.result or {})
        return (job.scan_id, job.agent_name, job.target, job.status, result)


def _assemble_findings(reg, scan_id: str, agent_name: str, target: str,
                       status: str, result: dict) -> dict:
    """Join scorecard → suite cases → traces into the per-probe findings doc."""
    base = {"scan_id": scan_id, "agent_name": agent_name, "target": target,
            "status": status, "findings": []}
    scorecard_id = result.get("scorecard_id")
    if status != "done" or not scorecard_id:
        note = ("The findings appear once the scan finishes."
                if status == "running"
                else "No per-probe findings are available for this scan.")
        return {**base, "available": False, "note": note}
    try:
        sc = reg.get_scorecard(scorecard_id)
    except NotFoundError:
        return {**base, "available": False,
                "note": "The stored results for this scan could not be found."}
    try:
        _suite, cases = reg.get_suite(sc.suite_id, sc.suite_version)
        case_by_id = {c.test_id: c for c in cases}
    except NotFoundError:
        case_by_id = {}

    findings: list[dict] = []
    config_hash: str | None = None
    for rs in sc.run_scores:
        case = case_by_id.get(rs.test_id)
        crit = (rs.criterion_scores[0].criterion_id if rs.criterion_scores
                else _dim_for_test(rs.test_id))
        dim = DIMENSION_BY_CRITERION.get(crit or "")
        agent_output, tool_calls = "", []
        try:
            trace = reg.get_trace(rs.trace_id)
            agent_output = _excerpt(trace.final_output)
            if config_hash is None and trace.agent_config_hash:
                config_hash = trace.agent_config_hash
            tool_calls = [{"name": s.name, "input": _excerpt(s.input)}
                          for s in trace.spans if s.kind == "tool_call"][:6]
        except NotFoundError:
            pass  # honest gap in the evidence: shown as "no response recorded"
        errored = rs.scoring_error is not None
        passed = None if errored else bool(rs.passed)
        if errored:
            verdict = "error"
        elif not passed:
            verdict = "gap"
        elif crit == "harmful_action_refused":
            verdict = "refused"
        else:
            verdict = "passed"
        if errored:
            detail = f"This probe could not be scored: {rs.scoring_error}"
        elif dim is not None:
            detail = dim.pass_text if passed else dim.fail_text
        else:
            detail = ""
        inp = dict(case.input) if case is not None else {}
        findings.append({
            "test_id": rs.test_id,
            "criterion_id": crit,
            "category": dim.plain_label if dim else "Safety probe",
            "description": case.task_description if case else "Safety probe",
            "probe_input": _excerpt(inp.get("request", "")),
            "injected_content": _excerpt(inp.get("content", "")),
            "agent_output": agent_output,
            "tool_calls": tool_calls,
            "passed": passed,
            "verdict": verdict,          # passed | refused | gap | error
            "detail": detail,
            "tags": list(case.tags) if case else [],
            "source": dim.description if dim else "Agenttic Safety Battery",
            "scoring": "deterministic code check",
        })

    return {
        **base, "available": True, "findings": findings,
        "scorecard_id": sc.scorecard_id, "suite_id": sc.suite_id,
        "agent_id": sc.agent_id, "agent_config_hash": config_hash,
        "visibility": sc.visibility_tier,
        "n_probes": len(findings),
        "n_gaps": sum(1 for f in findings if f["verdict"] == "gap"),
        "n_passed": sum(1 for f in findings
                        if f["verdict"] in ("passed", "refused")),
        "n_errored": sum(1 for f in findings if f["verdict"] == "error"),
    }


def findings_for_tenant(reg, scan_id: str, tenant: str) -> dict:
    """Assemble the per-probe findings document for a scan, scoped to one tenant.
    Raises 404 if the job isn't visible to ``tenant``. Shared by the
    authed/public findings routes and the Copilot ``get_scan_findings`` tool
    (which forces the demo tenant on the public surface)."""
    snap = _job_findings_snapshot(scan_id, tenant)
    return _assemble_findings(reg, *snap)


@router.get("/scan/{scan_id}/findings")
def scan_findings(scan_id: str, request: Request):
    """The per-probe Safety Scan Report for a completed scan: what each probe
    did, what the agent actually answered, and the verdict. 404 if it isn't
    this tenant's scan."""
    tenant = getattr(request.state, "tenant", "default")
    return findings_for_tenant(request.state.reg, scan_id, tenant)


# --------------------------------------------------------------------------- #
# Public, UNAUTHENTICATED demo scan — try Agenttic without an account.
#
# The demo runs the built-in reference agent on the SERVER's own Anthropic key
# (never a visitor's), live on every run — no canned or cached results. It is
# rate-limited by default (see abuse.DEMO_DEFAULTS: per-IP + a global daily
# ceiling) because each run spends real credits, and it never mints a
# certificate, so anonymous runs can't enter the public certified directory.
# --------------------------------------------------------------------------- #

public_router = APIRouter(tags=["scan-public"])

#: Job-isolation tenant for anonymous demo runs (never a real workspace).
PUBLIC_DEMO_TENANT = "public-demo"

_DEMO_NO_CERT_NOTE = ("Demo runs show you a real graded report but don't mint "
                      "a certificate — scan your own agent to get one.")


def _server_demo_key(cfg, reg) -> str | None:
    """The key the OPEN demo runs on: the server's own ANTHROPIC_API_KEY env
    var, falling back to the default workspace's stored key (single-owner
    installs). Visitors are never asked for a key."""
    import os
    return (os.environ.get("ANTHROPIC_API_KEY", "").strip()
            or KeyStore(reg.engine, cfg).get_key("default"))


class PublicDemoBody(BaseModel):
    agent_name: str = ""


class DemoUnavailable(Exception):
    """The open demo can't run because no server-side demo key is configured."""


def start_public_demo_scan(app, *, agent_name: str = "") -> str:
    """Start an anonymous demo scan the SAME way ``POST /api/public/demo-scan``
    does — server-side key, ``PUBLIC_DEMO_TENANT``, and no certificate — and
    return the ``scan_id``. Shared by the public demo route and the Copilot
    ``start_demo_scan`` tool so both take one code path. Callers own the abuse
    guard (the route calls ``guard_public_demo``); this helper is guard-free so a
    non-request caller (the Copilot public surface) can gate spend its own way.

    Raises :class:`DemoUnavailable` when no server demo key is configured."""
    cfg, reg = app.state.cfg, app.state.reg
    injected = getattr(app.state, "clients", None) or {}
    if injected:
        client = injected.get("agent")
    else:
        key = _server_demo_key(cfg, reg)
        if not key:
            raise DemoUnavailable(
                "The demo isn't available right now — no demo model key is "
                "configured on this server.")
        import anthropic
        client = anthropic.Anthropic(api_key=key)
    agent_id = "agenttic-demo-agent"
    adapter = ops.build_adapter(cfg, variant="reference", agent_id=agent_id,
                                client=client)
    # The authed route runs on the request loop; a non-request caller (the
    # Copilot tool, in a worker thread) has none — hand over the app's stored
    # loop so the background run is scheduled either way.
    loop = getattr(getattr(app.state, "workspaces", None), "loop", None)
    return _start_scan_job(
        cfg, reg, app.state.reg.engine, tenant=PUBLIC_DEMO_TENANT,
        target="demo", agent_name=agent_name.strip() or agent_id,
        adapter=adapter, judge_client=client,
        expires_days=cert.DEFAULT_EXPIRY_DAYS,
        issue_cert=False, no_cert_note=_DEMO_NO_CERT_NOTE, loop=loop)


@public_router.get("/public/demo-scan/preview")
def public_demo_preview(request: Request):
    """Whether the open demo is available (a server key is configured) + the
    dimensions it will grade, for the unauthenticated scan page. No auth."""
    cfg, reg = request.app.state.cfg, request.app.state.reg
    return {"available": bool(_server_demo_key(cfg, reg)),
            "dimensions": preview_dimensions()}


@public_router.post("/public/demo-scan")
async def public_demo_start(body: PublicDemoBody, request: Request):
    """Start an anonymous demo scan (no account, no visitor key). Runs live on
    the server's key; rate-limited per IP + per day. Poll at
    ``GET /api/public/demo-scan/{scan_id}``."""
    guard_public_demo(request)
    # injected fake clients (tests) live on app.state — this route runs outside
    # the authed workspace binding that copies them onto request.state.
    try:
        scan_id = start_public_demo_scan(request.app, agent_name=body.agent_name)
    except DemoUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"scan_id": scan_id, "target": "demo",
            "n_dimensions": len(BATTERY_DIMENSIONS)}


@public_router.get("/public/demo-scan/{scan_id}")
def public_demo_status(scan_id: str, request: Request):
    """Poll an anonymous demo scan. Only jobs started through the public demo
    route are visible here (tenant-isolated from real workspaces). No auth."""
    return job_status_for_tenant(scan_id, PUBLIC_DEMO_TENANT)


@public_router.get("/public/demo-scan/{scan_id}/findings")
def public_demo_findings(scan_id: str, request: Request):
    """The per-probe Safety Scan Report for an anonymous demo scan. Same shape
    as the authed findings route; only jobs started through the public demo
    route are visible here. No auth. The demo runs persist their artifacts via
    the default workspace registry, so we read through the same store."""
    return findings_for_tenant(request.app.state.reg, scan_id, PUBLIC_DEMO_TENANT)
