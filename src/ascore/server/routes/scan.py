"""Public "Scan my agent" convenience endpoint — the consumer on-ramp.

A normal user points us at their agent (an HTTP endpoint, with an optional auth
header) or picks the built-in demo agent, and gets back a signed A–F safety
grade. This route is a THIN orchestrator over the existing engine:

    POST /api/scan            start a scan (background); returns a scan_id
    GET  /api/scan/{scan_id}  poll live progress + the graded result + cert
    GET  /api/scan/preview    what a scan will do (dimensions, key/cost) before running

The heavy lifting is ``ascore.scan.run_safety_scan`` (build adapter → run + score
the Safety Battery → grade) and ``server.certifications.issue_certificate`` (the
signed, tamper-evident certificate). We add no scoring here.

Honesty / cost:
* A scan against the user's OWN endpoint (``target=endpoint``) spends NO Anthropic
  tokens and needs NO key — the agent runs on the user's infra and the safety
  battery is scored by deterministic code checks.
* The demo agent (``target=demo``) runs the built-in reference agent with the
  tenant's OWN Anthropic key (BYO-key); we surface a clear "add your key" path
  and never fall back to a shared key.

Progress is exposed by polling (simple + robust for a short consumer scan). Jobs
live in-process; the durable artifacts (scorecard + signed certificate) are
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

from ascore import certification as cert
from ascore import ops, scan
from ascore.metrics.safety_battery import BATTERY_DIMENSIONS
from ascore.server.auth import require_operator
from ascore.server.certifications import issue_certificate
from ascore.server.keys import NO_KEY_MSG, KeyStore

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
    certificate: dict | None = None  # the issued public certificate view
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
    agent_name: str = ""              # display name for the certificate
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
        from ascore.connect import build_connection_adapter
        from ascore.server.connections import ConnectionStore
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
    dims = [{"criterion_id": d.criterion_id, "label": d.plain_label,
             "critical": d.criterion_id in
             {BATTERY_DIMENSIONS[0].criterion_id, BATTERY_DIMENSIONS[1].criterion_id}}
            for d in BATTERY_DIMENSIONS]
    return {
        "dimensions": dims,
        "endpoint": {
            "needs_key": False,
            "note": "We send the safety probes to your endpoint and grade the "
                    "answers. No Anthropic key and no Agenttic spend — your agent "
                    "runs on your own infrastructure.",
        },
        "demo": {
            "needs_key": True,
            "key_set": key_set,
            "note": "Runs the built-in demo agent with your own Anthropic key so "
                    "you can see a real grade with zero setup. A handful of short "
                    "calls — typically a few cents.",
        },
    }


@router.post("/scan", dependencies=[Depends(require_operator)])
async def start_scan(body: ScanBody, request: Request):
    """Start a safety scan (runs in the background). Returns a ``scan_id`` to poll
    at ``GET /api/scan/{scan_id}``."""
    cfg, reg = request.state.cfg, request.state.reg
    tenant = getattr(request.state, "tenant", "default")
    # A demo scan spends the tenant's metered model budget, so gate it on credits
    # (endpoint/connection scans run on the user's own infra → no gate).
    if (body.target or "endpoint").lower() == "demo":
        from ascore.billing import service as billing_service
        try:
            billing_service.ensure_credits(reg.engine, tenant, cfg)
        except billing_service.OutOfCreditsError as exc:
            raise HTTPException(402, str(exc))
    adapter, judge_client, agent_id = _build_scan_adapter(request, body)
    # Gentle traffic against a user's live agent: force sequential (1-in-flight)
    # requests. Per-request timeout + rate limit are set on the connection adapter.
    if (body.target or "").lower() == "connection":
        from ascore.connect import gentle_scan_cfg
        cfg = gentle_scan_cfg(cfg)

    scan_id = "scan_" + uuid.uuid4().hex[:16]
    job = ScanJob(scan_id=scan_id, tenant=tenant, target=body.target,
                  agent_name=body.agent_name.strip() or agent_id,
                  checks=_initial_checks())
    with _LOCK:
        _JOBS[scan_id] = job

    global_engine = request.app.state.reg.engine
    expires_days = body.expires_days

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
            try:
                view = issue_certificate(
                    global_engine=global_engine, cfg=cfg, reg=reg, tenant=tenant,
                    scorecard_id=result["scorecard_id"], expires_days=expires_days)
                with _LOCK:
                    job.certificate = view
            except cert.CertificationError as exc:
                with _LOCK:
                    job.cert_note = (
                        "We graded your agent but couldn't issue a certificate: "
                        f"{exc}")
            except Exception as exc:  # noqa: BLE001 — cert is best-effort
                logger.error("scan %s cert issue failed: %s", scan_id, exc)
                with _LOCK:
                    job.cert_note = "We graded your agent; certificate issuance " \
                        "is temporarily unavailable."
            # Meter the tenant's model spend for this scan as a credit debit
            # (best-effort). Black-box/endpoint scans report $0 (they run on the
            # user's own infra) and so debit nothing.
            scan_cost = float(result.get("cost_usd") or 0.0)
            if scan_cost > 0:
                try:
                    from ascore.billing import service as billing_service
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

    asyncio.create_task(_run())
    return {"scan_id": scan_id, "target": body.target,
            "n_dimensions": len(BATTERY_DIMENSIONS)}


@router.get("/scan/{scan_id}")
def scan_status(scan_id: str, request: Request):
    """Poll a scan: live phase/progress, the per-dimension checklist, and (once
    done) the grade + signed certificate. 404 if it isn't this tenant's scan."""
    tenant = getattr(request.state, "tenant", "default")
    with _LOCK:
        job = _JOBS.get(scan_id)
        if job is None or job.tenant != tenant:
            raise HTTPException(404, f"scan {scan_id} not found")
        return job.public()
