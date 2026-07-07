"""Card autofill from Agenttic's own evidence (SPEC-2 T20.1).

Every field this produces is ``measured`` — its value is derived from persisted
Agenttic data and carries evidence refs that resolve to ids (Hard Rule 15):

* **models** — the agent's model, from config / the declared agent.
* **action space** — the distinct tools observed across the agent's traces.
* **benchmarks** — task-success from the agent's scorecards.
* **incidents** — the agent's incidents from the registry.
* **monitoring** — whether the live-monitoring path has data for the agent.
* **certification** — the agent's latest dossier (tier + status).

Fields with no backing data are simply omitted (no refs ⇒ no value) — never
fabricated.
"""

from __future__ import annotations

from ascore.schema.agent_card import AgentCard, FieldValue

# canonical field keys (from cards.fields.generate_field_registry)
K_MODEL = "technical_capabilities_system_architecture.model_specifications"
K_ACTION = "technical_capabilities_system_architecture.action_space"
K_BENCH = "safety_evaluation_impact.benchmark_performance_and_demonstrated_capabilities"
K_INCIDENTS = "safety_evaluation_impact.any_known_incidents_or_reported_vulnerabilities"
K_MONITORING = "autonomy_control.execution_monitoring_traces_and_transparency"
K_CERT = "safety_evaluation_impact.internal_safety_evaluations_and_results"
K_NAME = "product_overview.name_of_agent"
K_APPROVAL = "autonomy_control.user_approval_requirements_for_different_decision_types"


def _model_field(cfg, reg, agent_id):
    model = None
    refs: list[str] = []
    try:
        agent = reg.get_declared_agent(agent_id)
        model = getattr(agent, "model", None) or getattr(
            getattr(agent, "config", None), "model", None)
        refs.append(f"declared_agent:{agent_id}")
    except Exception:  # noqa: BLE001
        pass
    if not model:
        model = (cfg or {}).get("models", {}).get("agent_default")
        if model:
            refs.append("config:models.agent_default")
    if model and refs:
        return FieldValue.measured(K_MODEL, model, refs)
    return None


def _action_space_field(reg, agent_id):
    tools: set[str] = set()
    refs: list[str] = []
    try:
        traces = list(reg.traces(agent_id, mode="batch"))
    except Exception:  # noqa: BLE001
        traces = []
    for t in traces[:200]:
        used = False
        for span in t.spans:
            if span.kind == "tool_call":
                tools.add(span.name)
                used = True
        if used:
            refs.append(f"trace:{t.trace_id}")
    if tools and refs:
        return FieldValue.measured(K_ACTION, sorted(tools), refs[:20])
    return None


def _benchmark_field(reg, agent_id):
    try:
        cards = reg.scorecards_for(agent_id)
    except Exception:  # noqa: BLE001
        cards = []
    if not cards:
        return None
    refs = [f"scorecard:{sc.scorecard_id}" for sc in cards]
    summary = {sc.suite_id: round(sc.task_success_rate, 3) for sc in cards}
    return FieldValue.measured(K_BENCH, summary, refs[:20])


def _incidents_field(reg, agent_id):
    try:
        incidents = reg.list_incidents(agent_id)
    except Exception:  # noqa: BLE001
        incidents = []
    if not incidents:
        # confirmed_none needs evidence; we can only say none_found here (we
        # haven't proven the agent has zero incidents, just none recorded).
        return FieldValue.none_found(K_INCIDENTS)
    refs = [f"incident:{i['incident_id']}" for i in incidents]
    value = [{"id": i["incident_id"], "severity": i["severity"]} for i in incidents]
    return FieldValue.measured(K_INCIDENTS, value, refs)


def _monitoring_field(reg, agent_id):
    refs: list[str] = []
    try:
        if list(reg.traces(agent_id, mode="live")):
            refs.append(f"live_traces:{agent_id}")
    except Exception:  # noqa: BLE001
        pass
    try:
        if reg.reeval_requests(agent_id):
            refs.append(f"reeval:{agent_id}")
    except Exception:  # noqa: BLE001
        pass
    if refs:
        return FieldValue.measured(K_MONITORING, "live monitoring active", refs)
    return None


def _certification_field(reg, agent_id):
    try:
        d = reg.latest_dossier(agent_id)
    except Exception:  # noqa: BLE001
        return None
    from ascore.certification.staleness import status
    value = {"tier": d.tier_decision.tier, "status": status(reg, d)}
    return FieldValue.measured(K_CERT, value, [f"dossier:{d.dossier_id}"])


def _approval_gates_field(reg, agent_id):
    """Resolved approvals are MEASURED evidence for the card's approval-gates
    field (SPEC-2 T26.3): they prove which decision types are approval-gated."""
    try:
        approvals = [a for a in reg.list_approvals()
                     if a.get("agent_id") == agent_id
                     and a.get("state") in ("approved", "denied", "expired")]
    except Exception:  # noqa: BLE001
        return None
    if not approvals:
        return None
    refs = [f"approval:{a['approval_id']}" for a in approvals]
    by_outcome: dict[str, int] = {}
    gated_classes: set[str] = set()
    for a in approvals:
        by_outcome[a["state"]] = by_outcome.get(a["state"], 0) + 1
        gated_classes.add(a.get("action_class", "unknown"))
    value = {"gated_action_classes": sorted(gated_classes),
             "resolutions": by_outcome}
    return FieldValue.measured(K_APPROVAL, value, refs[:20])


def autofill_card(cfg, reg, agent_id: str) -> AgentCard:
    """Build a card for ``agent_id`` from Agenttic's own measured evidence."""
    fields: dict[str, FieldValue] = {}
    fields[K_NAME] = FieldValue.documented(K_NAME, agent_id, [f"agent_id:{agent_id}"])
    for fv in [
        _model_field(cfg, reg, agent_id),
        _action_space_field(reg, agent_id),
        _benchmark_field(reg, agent_id),
        _incidents_field(reg, agent_id),
        _monitoring_field(reg, agent_id),
        _certification_field(reg, agent_id),
        _approval_gates_field(reg, agent_id),
    ]:
        if fv is not None:
            fields[fv.field_key] = fv
    return AgentCard(agent_id=agent_id, source="agenttic", fields=fields)
