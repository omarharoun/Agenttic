"""Pre-run cost estimation.

Projects the spend of running a suite against an agent *before* it executes, so
operators see expected cost up front and the budget gate (``ascore.budget``) can
warn/abort. The estimate is a prior built from config (`cost.*`) refined by the
actual case count and average input size; it is deliberately a rough upper-ish
bound, not a promise. Actual cost is measured during the run and reported
alongside the estimate.

Black-box agents expose no token usage, so their *agent* cost is reported as
unknown (0 + a note); judge cost is still estimated.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ascore.pricing import token_cost
from ascore.registry.sqlite_store import NotFoundError, Registry
from ascore.scoring.engine import applicable_criteria

_CHARS_PER_TOKEN = 4


@dataclass
class CostEstimate:
    n_cases: int
    agent_variant: str
    agent_model: str | None
    agent_calls_per_case: int
    n_judge_criteria: int
    judge_model: str | None
    projected_agent_usd: float
    projected_judge_usd: float
    projected_usd: float
    assumptions: dict
    notes: list[str] = field(default_factory=list)

    def model_dump(self) -> dict:
        return asdict(self)


def judge_model_for(cfg: dict, agent_model: str | None) -> str:
    """Mirror make_judge's tier choice for estimation purposes."""
    executor = cfg["models"].get("judge_executor")
    strong = cfg["models"]["judge_strong"]
    if executor and executor != agent_model and strong != agent_model:
        return executor
    return strong


def estimate_run_cost(
    cfg: dict, *,
    n_cases: int,
    agent_variant: str,
    agent_model: str | None,
    n_judge_criteria: int = 0,
    judge_model: str | None = None,
    avg_input_chars: int = 0,
    bb_call_cost: float = 0.0,
) -> CostEstimate:
    c = cfg.get("cost", {}) or {}
    steps = int(c.get("expected_agent_steps", 3))
    in_tok = int(c.get("expected_input_tokens", 600)) + avg_input_chars // _CHARS_PER_TOKEN
    out_tok = int(c.get("expected_output_tokens", 250))
    j_in = int(c.get("judge_input_tokens", 700)) + avg_input_chars // _CHARS_PER_TOKEN
    j_out = int(c.get("judge_output_tokens", 120))
    notes: list[str] = []

    if agent_variant == "blackbox":
        agent_calls = 1
        if bb_call_cost:
            agent_usd = bb_call_cost * n_cases
            notes.append("black-box agent cost from the declared per-call cost.")
        else:
            agent_usd = 0.0
            notes.append("black-box agent cost is unknown (no token usage "
                         "exposed and none declared); only judge cost is estimated.")
    else:
        agent_calls = steps
        per_call = token_cost(cfg, agent_model, in_tok, out_tok)
        agent_usd = per_call * agent_calls * n_cases
        if agent_variant == "managed":
            notes.append("managed-agent model assumed = agent_default for pricing.")

    judge_usd = 0.0
    if n_judge_criteria and judge_model:
        judge_usd = token_cost(cfg, judge_model, j_in, j_out) * n_judge_criteria * n_cases
        notes.append("advisor consults on borderline judge calls may add cost "
                     "beyond this estimate.")

    return CostEstimate(
        n_cases=n_cases, agent_variant=agent_variant, agent_model=agent_model,
        agent_calls_per_case=agent_calls, n_judge_criteria=n_judge_criteria,
        judge_model=judge_model if n_judge_criteria else None,
        projected_agent_usd=round(agent_usd, 6),
        projected_judge_usd=round(judge_usd, 6),
        projected_usd=round(agent_usd + judge_usd, 6),
        assumptions={"expected_agent_steps": steps,
                     "expected_input_tokens": in_tok,
                     "expected_output_tokens": out_tok,
                     "judge_input_tokens": j_in, "judge_output_tokens": j_out},
        notes=notes)


def _resolve_agent(cfg: dict, reg: Registry, agent_id: str | None,
                   agent_model: str | None) -> tuple[str, str | None, str, float]:
    """(variant, model, visibility, bb_call_cost) for an agent_id — from the
    declared catalog if present, else a reference agent on the default model."""
    from ascore.ops import blackbox_call_cost
    variant, visibility, bb_cost = "reference", "glass_box", 0.0
    model = agent_model or cfg["models"]["agent_default"]
    if agent_id:
        try:
            d = reg.get_declared_agent(agent_id)
            variant = d.variant
            if variant == "reference":
                model = agent_model or d.model or cfg["models"]["agent_default"]
            elif variant == "blackbox":
                model, visibility = None, "black_box"
                bb_cost = blackbox_call_cost(
                    cfg, cost_per_call_usd=d.cost_per_call_usd, model=d.model,
                    expected_input_tokens=d.expected_input_tokens,
                    expected_output_tokens=d.expected_output_tokens)
            elif variant == "managed":
                model = agent_model or cfg["models"]["agent_default"]
        except NotFoundError:
            pass
    return variant, model, visibility, bb_cost


def estimate_for_run(cfg: dict, reg: Registry, suite_id: str, *,
                     variant: str, model: str | None,
                     with_judge: bool = True,
                     bb_call_cost: float = 0.0,
                     version: int | None = None) -> CostEstimate:
    """Estimate for a concrete (variant, model) — used by the budget gate where
    the adapter is already built, so the variant/model are known exactly."""
    _, cases = reg.get_suite(suite_id, version)
    visibility = "black_box" if variant == "blackbox" else "glass_box"
    n_judge = 0
    if with_judge and cases:
        try:
            rubric = reg.get_rubric(cases[0].rubric_id)
            criteria = applicable_criteria(rubric, visibility)
            n_judge = sum(1 for c in criteria if c.scorer == "judge")
        except (NotFoundError, ValueError):
            n_judge = 0
    avg_chars = (sum(len(json.dumps(c.input)) for c in cases) // len(cases)
                 if cases else 0)
    return estimate_run_cost(
        cfg, n_cases=len(cases), agent_variant=variant, agent_model=model,
        n_judge_criteria=n_judge, judge_model=judge_model_for(cfg, model),
        avg_input_chars=avg_chars, bb_call_cost=bb_call_cost)


def estimate_for_suite(cfg: dict, reg: Registry, suite_id: str, *,
                       agent_id: str | None = None,
                       agent_model: str | None = None,
                       with_judge: bool = True,
                       version: int | None = None) -> CostEstimate:
    variant, model, _, bb_cost = _resolve_agent(cfg, reg, agent_id, agent_model)
    return estimate_for_run(cfg, reg, suite_id, variant=variant, model=model,
                            with_judge=with_judge, bb_call_cost=bb_cost,
                            version=version)


def estimate_for_workflow(cfg: dict, reg: Registry, wf) -> CostEstimate:
    """Estimate a workflow by finding its agent + run-suite + score nodes."""
    from ascore.ops import blackbox_call_cost
    nodes = {n.type: n for n in wf.nodes}
    agent_node = nodes.get("agent")
    suite_id = None
    for n in wf.nodes:
        if n.type in ("run_suite", "generator") and n.config.get("suite_id"):
            suite_id = n.config["suite_id"]
            break
    if not suite_id:
        raise ValueError("workflow has no run_suite/generator node with a suite_id")
    ac = agent_node.config if agent_node else {}
    agent_id = ac.get("agent_id")
    agent_model = ac.get("model") or None
    variant = ac.get("variant", "reference")
    with_judge = "score" in nodes
    # ad-hoc black-box node cost hints (declared agents resolve via agent_id)
    if variant == "blackbox" and any(ac.get(k) for k in
            ("cost_per_call_usd", "expected_input_tokens", "expected_output_tokens")):
        model = agent_model
        bb_cost = blackbox_call_cost(
            cfg, cost_per_call_usd=ac.get("cost_per_call_usd", 0.0),
            model=agent_model or "",
            expected_input_tokens=ac.get("expected_input_tokens", 0),
            expected_output_tokens=ac.get("expected_output_tokens", 0))
        return estimate_for_run(cfg, reg, suite_id, variant=variant, model=model,
                                with_judge=with_judge, bb_call_cost=bb_cost)
    return estimate_for_suite(cfg, reg, suite_id, agent_id=agent_id,
                              agent_model=agent_model, with_judge=with_judge)
