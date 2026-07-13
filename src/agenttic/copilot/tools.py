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
  proposes; :mod:`ascore.copilot.agent` gates on the human decision) AND the
  credits gate allows the spend.

Each tool's ``run`` returns a small JSON-able dict (or ``{"error": ...}``); the
agent stringifies + secret-scrubs it before it re-enters the model context as
UNTRUSTED data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ascore.server.auth import ROLES


# --------------------------------------------------------------------------- #
# Tool context — the per-request, tenant-scoped handle every tool runs against.
# --------------------------------------------------------------------------- #


@dataclass
class ToolContext:
    """Captures the request's tenant-scoped state so tools call real internals."""
    request: Any

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
    from ascore.server.routes.dossiers import list_dossiers
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
    from ascore.registry.sqlite_store import NotFoundError
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
    from ascore.certification.dossier import verify_dossier
    from ascore.registry.sqlite_store import NotFoundError
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
    from ascore.server.keys import KeyStore
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
    from ascore.server.keys import tenant_run_clients
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


def _run_revoke_certification(ctx: ToolContext, args: dict) -> dict:
    if not ctx.is_operator():
        return {"error": "this action requires the 'operator' role"}
    from ascore.server.certifications import CertStore
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
