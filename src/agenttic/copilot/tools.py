"""Copilot tools = the real Agenttic API, scoped to the signed-in user.

Every tool runs **in-process against the same tenant-scoped objects the HTTP
routes use** (``request.state.reg`` / ``certifier`` / ``cfg`` / role), so the
agent can never exceed what the user could do themselves: same tenant, same auth,
same budget (a real run uses the tenant's own Anthropic key), same role checks.
We call the platform's own code — no invented endpoints.

Tools are split by ``kind``:

* ``read``  — safe, side-effect-free lookups. The agent runs them freely.
* ``write`` — spend budget or mutate state. These carry a ``confirm`` builder and
  are NEVER executed until the user explicitly confirms in the UI (the agent
  proposes; :mod:`agenttic.copilot.agent` gates on the human decision) AND the
  credits gate allows the spend.

Each tool's ``run`` returns a small JSON-able dict (or ``{"error": ...}``); the
agent stringifies + secret-scrubs it before it re-enters the model context as
UNTRUSTED data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agenttic.server.auth import ROLES


# --------------------------------------------------------------------------- #
# Tool context — the per-request, tenant-scoped handle every tool runs against.
# --------------------------------------------------------------------------- #


@dataclass
class ToolContext:
    """Captures the request's tenant-scoped state so tools call real internals.

    ``public=True`` marks the anonymous/public bot surface: there is no signed-in
    tenant binding on ``request.state``, so demo-scan tools read app state
    (``request.app.state``) and force the public-demo tenant. Only tools in
    :data:`PUBLIC_TOOL_NAMES` may run in that mode; the endpoint author is
    responsible for filtering the tool set to that allowlist."""
    request: Any
    #: True on the anonymous public surface (demo-only, forced demo tenant).
    public: bool = False

    @property
    def state(self):
        return self.request.state

    @property
    def tenant(self) -> str:
        return getattr(self.state, "tenant", "default")

    @property
    def role(self) -> str | None:
        return getattr(self.state, "role", None)

    @property
    def reg(self):
        return self.state.reg

    @property
    def cfg(self) -> dict:
        return getattr(self.state, "cfg", None) or {}

    @property
    def certifier(self):
        return getattr(self.state, "certifier", None)

    def is_operator(self) -> bool:
        return ROLES.get(self.role or "", -1) >= ROLES["operator"]


# --------------------------------------------------------------------------- #
# Tool definition.
# --------------------------------------------------------------------------- #


@dataclass
class Tool:
    name: str
    kind: str                       # "read" | "write"
    description: str
    input_schema: dict
    run: Callable[[ToolContext, dict], Any]
    #: For write tools: build the human-facing confirmation card from the args.
    confirm: Callable[[dict], dict] | None = None

    @property
    def is_write(self) -> bool:
        return self.kind == "write"


_REGISTRY: dict[str, Tool] = {}


def _register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def tool_schemas() -> list[dict]:
    """Anthropic tool-use schema list for every registered tool."""
    return [{"name": t.name, "description": t.description,
             "input_schema": t.input_schema} for t in _REGISTRY.values()]


def is_write(name: str) -> bool:
    t = _REGISTRY.get(name)
    return bool(t and t.is_write)


def confirmation_for(name: str, args: dict) -> dict | None:
    t = _REGISTRY.get(name)
    if t is None or not t.is_write or t.confirm is None:
        return None
    card = t.confirm(dict(args or {}))
    card.setdefault("tool", name)
    return card


# --------------------------------------------------------------------------- #
# READ tools — run freely, no confirmation. Side-effect-free lookups.
# --------------------------------------------------------------------------- #


def _run_platform_status(ctx: ToolContext, args: dict) -> dict:
    checker = getattr(ctx.request.app.state, "health", None)
    if checker is None:
        return {"error": "status checker unavailable"}
    snap = checker.snapshot(ctx.request.app)
    return {"status": snap.get("status"), "version": snap.get("version"),
            "uptime_seconds": snap.get("uptime_seconds"),
            "components": [{"name": c.get("name"), "status": c.get("status")}
                           for c in snap.get("components", [])]}


_register(Tool(
    name="platform_status", kind="read",
    description="Get Agenttic's own live service status (overall state, version, "
                "uptime, and per-component health). Use to answer 'is the platform "
                "up?' Never fabricate a status — report exactly what this returns.",
    input_schema={"type": "object", "properties": {}},
    run=_run_platform_status))


def _run_list_agents(ctx: ToolContext, args: dict) -> dict:
    rows = ctx.reg.list_declared_agents(include_retired=bool(args.get("include_retired")))
    agents = [{"agent_id": r.get("agent_id"), "name": r.get("name"),
               "version": r.get("version"), "model": r.get("model")}
              for r in rows]
    return {"agents": agents, "count": len(agents)}


_register(Tool(
    name="list_agents", kind="read",
    description="List the agents registered in this workspace (the user's tenant) "
                "with their id/name/version/model. Use to find an agent_id to act "
                "on, or to answer 'what agents do I have?'",
    input_schema={"type": "object", "properties": {
        "include_retired": {"type": "boolean",
                            "description": "include soft-deleted agents"}}},
    run=_run_list_agents))


def _run_list_profiles(ctx: ToolContext, args: dict) -> dict:
    profiles = (ctx.cfg.get("certification", {}) or {}).get("profiles", {}) or {}
    out = [{"profile_id": pid,
            "required_domains": p.get("required_domains", []),
            "thresholds": p.get("thresholds", {}),
            "min_k": p.get("min_k")}
           for pid, p in profiles.items()]
    return {"profiles": out, "count": len(out)}


_register(Tool(
    name="list_certification_profiles", kind="read",
    description="List the certification profiles available (pinned recipes: "
                "required domains + thresholds). Use to pick a profile_id before "
                "proposing a certification run.",
    input_schema={"type": "object", "properties": {}},
    run=_run_list_profiles))


def _run_get_profile(ctx: ToolContext, args: dict) -> dict:
    pid = str(args.get("profile_id", "")).strip()
    profiles = (ctx.cfg.get("certification", {}) or {}).get("profiles", {}) or {}
    p = profiles.get(pid)
    if p is None:
        return {"error": f"profile {pid!r} is not defined",
                "available": list(profiles.keys())}
    return {"profile_id": pid, **p}


_register(Tool(
    name="get_certification_profile", kind="read",
    description="Get one certification profile's full composition — its required "
                "domains and thresholds. Cite these exactly; do not invent "
                "thresholds.",
    input_schema={"type": "object", "properties": {
        "profile_id": {"type": "string"}}, "required": ["profile_id"]},
    run=_run_get_profile))


def _run_list_dossiers(ctx: ToolContext, args: dict) -> dict:
    from agenttic.server.routes.dossiers import list_dossiers
    agent_id = args.get("agent_id") or None
    rows = list_dossiers(ctx.request, agent_id=agent_id)
    slim = [{"dossier_id": r.get("dossier_id"), "agent_id": r.get("agent_id"),
             "tier": r.get("tier"), "status": r.get("status"),
             "profile_id": r.get("profile_id")} for r in rows]
    return {"dossiers": slim, "count": len(slim)}


_register(Tool(
    name="list_dossiers", kind="read",
    description="List certification dossiers in this workspace (optionally for one "
                "agent_id): dossier_id, agent_id, tier (A/B/C), status. Use to find "
                "a dossier to fetch or verify.",
    input_schema={"type": "object", "properties": {
        "agent_id": {"type": "string", "description": "filter to one agent"}}},
    run=_run_list_dossiers))


def _run_get_dossier(ctx: ToolContext, args: dict) -> dict:
    from agenttic.registry.sqlite_store import NotFoundError
    did = str(args.get("dossier_id", "")).strip()
    try:
        d = ctx.reg.get_dossier(did)
    except NotFoundError:
        return {"error": f"dossier {did!r} not found in this workspace"}
    body = d.model_dump(mode="json")
    tier = (body.get("tier_decision") or {})
    # Return the honesty-relevant slice, not the whole (large) dossier.
    return {"dossier_id": body.get("dossier_id"), "agent_id": body.get("agent_id"),
            "profile_id": body.get("profile_id"),
            "tier": tier.get("tier"), "caps_applied": tier.get("caps_applied"),
            "floors_breached": tier.get("floors_breached"),
            "coverage": body.get("coverage"),
            "created_at": body.get("created_at")}


_register(Tool(
    name="get_dossier", kind="read",
    description="Fetch a certification dossier by id: its tier (A/B/C), any "
                "caps_applied / floors_breached, and per-domain coverage (incl. "
                "NOT ASSESSED / assessed_seed vs assessed_real). Report these "
                "verbatim; NEVER invent numbers a dossier doesn't contain.",
    input_schema={"type": "object", "properties": {
        "dossier_id": {"type": "string"}}, "required": ["dossier_id"]},
    run=_run_get_dossier))


def _run_verify_dossier(ctx: ToolContext, args: dict) -> dict:
    from agenttic.certification.dossier import verify_dossier
    from agenttic.registry.sqlite_store import NotFoundError
    did = str(args.get("dossier_id", "")).strip()
    try:
        d = ctx.reg.get_dossier(did)
    except NotFoundError:
        return {"error": f"dossier {did!r} not found in this workspace"}
    v = verify_dossier(d, ctx.reg)
    return {"dossier_id": did, "valid": getattr(v, "valid", None),
            "reason": getattr(v, "reason", None)}


_register(Tool(
    name="verify_dossier", kind="read",
    description="Recompute a dossier's hash chain offline and report whether it "
                "verifies (valid true/false + reason). Use to answer 'is this "
                "dossier authentic / untampered?'",
    input_schema={"type": "object", "properties": {
        "dossier_id": {"type": "string"}}, "required": ["dossier_id"]},
    run=_run_verify_dossier))


def _run_certify_job(ctx: ToolContext, args: dict) -> dict:
    if ctx.certifier is None:
        return {"error": "certification runner unavailable"}
    job = ctx.certifier.get(str(args.get("job_id", "")).strip())
    if job is None:
        return {"error": "job not found (it may be from another session or expired)"}
    return job


_register(Tool(
    name="get_certification_job", kind="read",
    description="Check the status of a certification job started earlier "
                "(running/succeeded/failed, and the resulting dossier_id + tier "
                "when done). Use to follow up after starting a certification.",
    input_schema={"type": "object", "properties": {
        "job_id": {"type": "string"}}, "required": ["job_id"]},
    run=_run_certify_job))


def _run_key_status(ctx: ToolContext, args: dict) -> dict:
    from agenttic.server.keys import KeyStore
    st = KeyStore(ctx.reg.engine, ctx.cfg).status(ctx.tenant)
    return {"anthropic_key_set": bool(st.get("set")), "masked": st.get("masked")}


_register(Tool(
    name="anthropic_key_status", kind="read",
    description="Check whether this workspace has an Anthropic API key configured "
                "(needed to run real certifications/scans). Returns only whether "
                "it's set and a masked hint — never the key. Use before proposing "
                "a run so you can tell the user if they need to add one first.",
    input_schema={"type": "object", "properties": {}},
    run=_run_key_status))


# --------------------------------------------------------------------------- #
# WRITE / COST tools — spend budget or mutate state. Confirmation REQUIRED.
# --------------------------------------------------------------------------- #


def _run_start_certification(ctx: ToolContext, args: dict) -> dict:
    """Launch an async certification job — the same path as POST /api/certify,
    with the same role + profile + tenant-key checks."""
    if not ctx.is_operator():
        return {"error": "this action requires the 'operator' role; the signed-in "
                         "user cannot start certifications"}
    profile_id = str(args.get("profile_id") or "cert-agent-safety-v1")
    agent_id = str(args.get("agent_id") or "ref-agent")
    defined = (ctx.cfg.get("certification", {}) or {}).get("profiles", {})
    if profile_id not in defined:
        return {"error": f"profile {profile_id!r} is not defined",
                "available": list(defined.keys())}
    from agenttic.server.keys import tenant_run_clients
    try:
        clients = tenant_run_clients(ctx.request)  # None when injected (tests/dev)
    except Exception as exc:  # noqa: BLE001 — surface the BYO-key gate as data
        return {"error": getattr(exc, "detail", None) or str(exc)}
    if clients is None:
        clients = getattr(ctx.state, "clients", None) or {}
    job_id = ctx.certifier.start(
        agent_id=agent_id, profile_id=profile_id,
        variant=str(args.get("variant") or "reference"),
        url=str(args.get("url") or ""),
        system_prompt=str(args.get("system_prompt") or ""),
        clients=clients or None, tenant=ctx.tenant, role=ctx.role)
    return {"started": True, "job_id": job_id, "agent_id": agent_id,
            "profile_id": profile_id,
            "note": "Certification is running asynchronously. Use "
                    "get_certification_job with this job_id to check progress."}


def _confirm_start_certification(args: dict) -> dict:
    agent_id = args.get("agent_id") or "ref-agent"
    profile_id = args.get("profile_id") or "cert-agent-safety-v1"
    return {
        "title": f"Run certification “{profile_id}” on {agent_id}?",
        "detail": f"This starts a full certification run of {agent_id} against "
                  f"{profile_id} (an elicitation matrix across the profile's "
                  "domains).",
        "cost_note": "Spends your Anthropic budget — it runs your agent and the "
                     "judge with your own key. Exact cost depends on the profile "
                     "and number of cases.",
        "risk": "medium",
    }


_register(Tool(
    name="start_certification", kind="write",
    description="Start a certification run for an agent against a profile → an "
                "evidence dossier (Tier A/B/C). This SPENDS the user's Anthropic "
                "budget and runs asynchronously. Propose it and let the user "
                "confirm; you cannot run it without their approval.",
    input_schema={"type": "object", "properties": {
        "agent_id": {"type": "string", "description": "agent to certify (default ref-agent)"},
        "profile_id": {"type": "string", "description": "profile id (default cert-agent-safety-v1)"},
        "variant": {"type": "string"}, "url": {"type": "string"},
        "system_prompt": {"type": "string"}}},
    run=_run_start_certification, confirm=_confirm_start_certification))


# --------------------------------------------------------------------------- #
# Safety-scan funnel tools.
#
# These drive the "Scan my agent" funnel the same way the HTTP routes do, by
# calling the route module's own shared helpers (never re-implementing scoring).
#
# Two surfaces run this registry:
#   * the AUTHED in-app Copilot — every tool, tenant-scoped to the signed-in user;
#   * the PUBLIC/anonymous bot — a strict allowlist (``PUBLIC_TOOL_NAMES`` below)
#     of demo-only tools, forced onto the public-demo tenant so it can only ever
#     see demo jobs. ``start_scan`` and every platform tool are excluded there.
#
# The demo tools reach the in-process scan job store (``_JOBS`` in scan.py) and
# the server-side demo key via ``ctx.request.app`` (app.state.cfg/reg/clients) —
# the SAME state the public demo route uses — so they work with no per-request
# tenant binding and no visitor key.
# --------------------------------------------------------------------------- #


def _run_preview_scan(ctx: ToolContext, args: dict) -> dict:
    """What a safety scan measures — the battery dimensions (READ)."""
    from agenttic.server.routes import scan as scan_mod
    dims = scan_mod.preview_dimensions()
    return {
        "dimensions": dims,
        "n_dimensions": len(dims),
        "note": "Each dimension is graded by a deterministic code check. The "
                "scan runs a battery of probes and reports which pass and which "
                "reveal a gap; the two critical dimensions gate the A–F grade.",
    }


_register(Tool(
    name="preview_scan", kind="read",
    description="Show what a safety scan measures before running one — the list "
                "of dimensions the Safety Battery grades (each with a plain "
                "label and whether it's a critical, grade-gating dimension). Use "
                "to answer 'what does a scan check?' Report these verbatim; do "
                "not invent dimensions.",
    input_schema={"type": "object", "properties": {}},
    run=_run_preview_scan))


def _run_start_demo_scan(ctx: ToolContext, args: dict) -> dict:
    """Start an anonymous demo scan — the SAME path as POST /api/public/demo-scan
    (server key, public-demo tenant, no certificate). Free demo; the public-safe
    action. Returns a scan_id to poll with get_scan_status / get_scan_findings."""
    from fastapi import HTTPException
    from agenttic.server.abuse import guard_public_demo
    from agenttic.server.routes import scan as scan_mod
    # The EXPENSIVE action (14 live probes) carries the tight demo ceiling —
    # per-IP + global/day (abuse.demo), same as POST /api/public/demo-scan. Chat
    # turns are limited separately and generously, so this only bites when a
    # visitor launches SCANS too fast; surface it as a clean tool error, not a 500.
    if getattr(ctx, "public", False):
        try:
            guard_public_demo(ctx.request)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {"error": detail.get("message") or "You've started a lot of "
                    "demo scans — give it a minute and try again."}
    try:
        scan_id = scan_mod.start_public_demo_scan(
            ctx.request.app, agent_name=str(args.get("agent_name") or ""))
    except scan_mod.DemoUnavailable as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — surface as data, never a 500
        return {"error": f"could not start the demo scan: {type(exc).__name__}"}
    return {"started": True, "scan_id": scan_id, "target": "demo",
            "note": "The demo scan is running. Use get_scan_status with this "
                    "scan_id to follow progress, then get_scan_findings for the "
                    "per-probe report. It grades a live run but mints no "
                    "certificate — scan your own agent to get one."}


_register(Tool(
    name="start_demo_scan", kind="write",
    description="Start a free anonymous demo scan of the built-in demo agent "
                "(runs live on the server's own key — no account, no API key, no "
                "certificate). Returns a scan_id to poll. This is the safe "
                "try-it action; propose it and let the user confirm.",
    input_schema={"type": "object", "properties": {
        "agent_name": {"type": "string",
                       "description": "display name for the demo run (optional)"}}},
    run=_run_start_demo_scan,
    confirm=lambda args: {
        "title": "Run a free demo scan?",
        "detail": "Runs the built-in demo agent through the full Safety Battery "
                  "live and shows you a graded, per-probe report. No account, no "
                  "API key, no certificate.",
        "cost_note": "Free to you — it runs on the server's demo key.",
        "risk": "low"}))


def _run_get_scan_status(ctx: ToolContext, args: dict) -> dict:
    """Poll a scan by id: status/phase/progress, the per-dimension checklist, and
    (once done) the grade. On the public surface the tenant is forced to the
    public-demo tenant, so only demo jobs are visible (READ)."""
    from fastapi import HTTPException
    from agenttic.server.routes import scan as scan_mod
    scan_id = str(args.get("scan_id", "")).strip()
    if not scan_id:
        return {"error": "scan_id is required"}
    tenant = scan_mod.PUBLIC_DEMO_TENANT if ctx.public else ctx.tenant
    try:
        job = scan_mod.job_status_for_tenant(scan_id, tenant)
    except HTTPException as exc:
        return {"error": exc.detail}
    result = job.get("result") or {}
    return {
        "scan_id": job.get("scan_id"), "status": job.get("status"),
        "phase": job.get("phase"), "progress": job.get("progress"),
        "target": job.get("target"), "agent_name": job.get("agent_name"),
        "checks": job.get("checks"),
        "grade": result.get("grade"),
        "composite_score": result.get("composite_score"),
        "grade_capped": result.get("grade_capped"),
        "cap_reason": result.get("cap_reason"),
        "error": job.get("error"), "cert_note": job.get("cert_note"),
    }


_register(Tool(
    name="get_scan_status", kind="read",
    description="Check a scan's live progress by scan_id: status "
                "(running/done/error), phase, progress bar, the per-dimension "
                "checklist, and — once finished — the A–F grade. Use to follow a "
                "scan you started. Report exactly what it returns.",
    input_schema={"type": "object", "properties": {
        "scan_id": {"type": "string"}}, "required": ["scan_id"]},
    run=_run_get_scan_status))


def _run_get_scan_findings(ctx: ToolContext, args: dict) -> dict:
    """Per-probe findings for a completed scan: which probes ran, which revealed a
    gap, and each probe's verdict. On the public surface the tenant is forced to
    the public-demo tenant (READ)."""
    from fastapi import HTTPException
    from agenttic.server.routes import scan as scan_mod
    scan_id = str(args.get("scan_id", "")).strip()
    if not scan_id:
        return {"error": "scan_id is required"}
    tenant = scan_mod.PUBLIC_DEMO_TENANT if ctx.public else ctx.tenant
    reg = ctx.request.app.state.reg if ctx.public else ctx.reg
    try:
        return scan_mod.findings_for_tenant(reg, scan_id, tenant)
    except HTTPException as exc:
        return {"error": exc.detail}


_register(Tool(
    name="get_scan_findings", kind="read",
    description="Get the per-probe Safety Scan Report for a completed scan: for "
                "each probe, what it did, what the agent answered, and the "
                "verdict (passed / refused / gap / error), plus counts of probes "
                "run and gaps found. Use after a scan finishes to explain the "
                "results. Report verdicts verbatim; never invent a gap.",
    input_schema={"type": "object", "properties": {
        "scan_id": {"type": "string"}}, "required": ["scan_id"]},
    run=_run_get_scan_findings))


def _run_start_scan(ctx: ToolContext, args: dict) -> dict:
    """Start a REAL safety scan against a signed-in user's own endpoint URL — the
    same path as POST /api/scan with target=endpoint. Authed-only; NOT exposed on
    the public surface. Runs on the user's own infra (no Anthropic key/spend)."""
    if ctx.public:
        return {"error": "this action is not available on the public demo surface"}
    if not ctx.is_operator():
        return {"error": "this action requires the 'operator' role; the signed-in "
                         "user cannot start scans"}
    url = str(args.get("url") or "").strip()
    if not url:
        return {"error": "an endpoint URL is required to scan your agent"}
    from agenttic.server.routes import scan as scan_mod
    from fastapi import HTTPException
    body = scan_mod.ScanBody(
        target="endpoint", url=url,
        header_name=str(args.get("header_name") or ""),
        header_value=str(args.get("header_value") or ""),
        agent_name=str(args.get("agent_name") or ""))
    try:
        adapter, judge_client, agent_id = scan_mod._build_scan_adapter(
            ctx.request, body)
    except HTTPException as exc:
        return {"error": exc.detail}
    # The Copilot tool runs off the event loop (worker thread) → hand over the
    # app's stored loop so the background scan is scheduled.
    loop = getattr(getattr(ctx.request.app.state, "workspaces", None),
                   "loop", None)
    scan_id = scan_mod._start_scan_job(
        ctx.cfg, ctx.reg, ctx.request.app.state.reg.engine, tenant=ctx.tenant,
        target="endpoint", agent_name=body.agent_name.strip() or agent_id,
        adapter=adapter, judge_client=judge_client,
        expires_days=body.expires_days, loop=loop)
    return {"started": True, "scan_id": scan_id, "target": "endpoint",
            "agent_name": body.agent_name.strip() or agent_id,
            "note": "The scan is running against your endpoint (on your own "
                    "infra — no Anthropic spend). Use get_scan_status with this "
                    "scan_id to follow progress and get_scan_findings for the "
                    "per-probe report."}


def _confirm_start_scan(args: dict) -> dict:
    url = args.get("url") or "your endpoint"
    return {
        "title": f"Scan your agent at {url}?",
        "detail": "Sends the Safety Battery probes to your endpoint and grades "
                  "the answers into an A–F safety report with a signed "
                  "certificate. Only run this against an agent you own or are "
                  "authorized to test.",
        "cost_note": "No Anthropic spend — your agent runs on your own "
                     "infrastructure and the battery is scored by code checks.",
        "risk": "medium",
    }


_register(Tool(
    name="start_scan", kind="write",
    description="Start a real safety scan against the signed-in user's own agent "
                "endpoint (a URL, with an optional auth header) → an A–F graded "
                "report + signed certificate. Runs on the user's own infra (no "
                "Anthropic spend). Propose it and let the user confirm; you "
                "cannot run it without their approval.",
    input_schema={"type": "object", "properties": {
        "url": {"type": "string", "description": "the agent's API endpoint URL"},
        "header_name": {"type": "string",
                        "description": "optional auth header name, e.g. Authorization"},
        "header_value": {"type": "string",
                         "description": "optional auth header value, e.g. Bearer sk-..."},
        "agent_name": {"type": "string",
                       "description": "display name for the certificate (optional)"}},
        "required": ["url"]},
    run=_run_start_scan, confirm=_confirm_start_scan))


# --------------------------------------------------------------------------- #
# Public allowlist — the STRICT set of tools the anonymous/public bot may use.
#
# ONLY the demo-scan funnel: preview + start a free demo + poll its status +
# read its per-probe findings. Every other tool (start_scan, list_agents,
# start_certification, dossiers, revoke, …) is EXCLUDED, so the public surface
# can never reach a real tenant, spend a user's budget, or touch certification.
# The public endpoint author must filter tool_schemas()/get_tool() to this set
# and construct the ToolContext with ``public=True`` + the demo tenant.
# --------------------------------------------------------------------------- #

PUBLIC_TOOL_NAMES: frozenset[str] = frozenset({
    "preview_scan", "start_demo_scan", "get_scan_status", "get_scan_findings",
})


def is_public_safe(name: str) -> bool:
    """Whether a tool may be exposed on the anonymous/public surface."""
    return name in PUBLIC_TOOL_NAMES


def public_tool_schemas() -> list[dict]:
    """Anthropic tool-use schema list restricted to the public allowlist."""
    return [{"name": t.name, "description": t.description,
             "input_schema": t.input_schema}
            for t in _REGISTRY.values() if t.name in PUBLIC_TOOL_NAMES]


def _run_revoke_certification(ctx: ToolContext, args: dict) -> dict:
    if not ctx.is_operator():
        return {"error": "this action requires the 'operator' role"}
    from agenttic.server.certifications import CertStore
    cert_id = str(args.get("cert_id", "")).strip()
    store = CertStore(ctx.request.app.state.reg.engine)
    if not store.revoke(tenant=ctx.tenant, cert_id=cert_id):
        return {"error": f"certificate {cert_id!r} not found, already revoked, or "
                         "not owned by this workspace"}
    return {"revoked": True, "cert_id": cert_id}


def _confirm_revoke_certification(args: dict) -> dict:
    cert_id = args.get("cert_id") or "?"
    return {
        "title": f"Revoke certificate {cert_id}?",
        "detail": "Revocation is immediate and append-only — there is no "
                  "un-revoke. The certificate will verify as 'revoked' everywhere.",
        "cost_note": "No spend, but this permanently changes trust state.",
        "risk": "high",
    }


_register(Tool(
    name="revoke_certification", kind="write",
    description="Revoke a safety certificate the workspace owns. Irreversible "
                "(append-only). Propose it and require the user's confirmation.",
    input_schema={"type": "object", "properties": {
        "cert_id": {"type": "string"}}, "required": ["cert_id"]},
    run=_run_revoke_certification, confirm=_confirm_revoke_certification))
