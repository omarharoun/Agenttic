"""Node type registry: every node the canvas can place.

Each NodeSpec couples a pydantic config model (its JSON schema drives the
UI's config form), typed input/output ports (edge validity = matching port
kinds), and an async ``run`` that calls the shared ops layer. Hard rules
stay enforced underneath: the gate lives in harness.run_suite, judge
separation in make_judge — no graph shape can bypass them.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel

from ascore import ops
from ascore.registry.sqlite_store import Registry
from ascore.schema.scorecard import RunScore


@dataclass
class NodeContext:
    cfg: dict
    reg: Registry
    execution_id: str
    node_id: str
    emit: Callable[[str, dict], None]                       # thread-safe -> EventBus
    wait_for_approval: Callable[[str, int], Awaitable[None]]
    cancelled: asyncio.Event
    clients: dict = field(default_factory=dict)             # test injection: agent/judge/anthropic


@dataclass(frozen=True)
class NodeSpec:
    type: str
    title: str
    category: str  # input | benchmark | agents | evaluation | delivery
    config_model: type[BaseModel]
    inputs: dict[str, str]    # port -> payload kind
    outputs: dict[str, str]
    run: Callable[[NodeContext, BaseModel, dict[str, Any]], Awaitable[dict[str, Any]]]
    description: str = ""


# -- configs ----------------------------------------------------------------

class BusinessDocConfig(BaseModel):
    text: str = ""
    file_path: str = ""


class GeneratorConfig(BaseModel):
    suite_id: str
    cases_per_task: int = 5


class HumanGateConfig(BaseModel):
    pass


class AgentConfig(BaseModel):
    variant: Literal["reference", "blackbox", "managed"] = "reference"
    agent_id: str = "agent-under-test"
    url: str = ""
    managed_agent_id: str = ""
    environment_id: str = ""
    agent_yaml_path: str = ""
    deploy: bool = False


class RunSuiteConfig(BaseModel):
    suite_id: str = ""          # optional override of the wired suite input
    version: int | None = None


class ScoreConfig(BaseModel):
    pass_threshold: float = 0.7


class ScorecardConfig(BaseModel):
    pass


class ReportConfig(BaseModel):
    out_path: str = ""


class MonitorConfig(BaseModel):
    agent_id: str = ""
    window: int = 50


# -- node implementations ----------------------------------------------------

async def _run_business_doc(ctx: NodeContext, cfg: BusinessDocConfig,
                            inputs: dict) -> dict:
    if cfg.file_path:
        doc = Path(cfg.file_path).read_text()
    elif cfg.text:
        doc = cfg.text
    else:
        raise ValueError("business_doc needs text or an uploaded file")
    return {"doc": doc}


async def _run_generator(ctx: NodeContext, cfg: GeneratorConfig,
                         inputs: dict) -> dict:
    def progress(t: str, d: dict) -> None:  # runs in the worker thread; emit is thread-safe
        msgs = {
            "tasks_extracted": f"extracted {d.get('total')} tasks",
            "criteria_defined": f"criteria for {d.get('task')} "
                                f"({d.get('n_criteria')} criteria)",
            "cases_generated": f"{d.get('n_cases')} cases for {d.get('task')} "
                               f"[{d.get('index', 0) + 1}/{d.get('total')}]",
        }
        ctx.emit("node_progress", {"event": t, "message": msgs.get(t, t), **d})

    suite = await asyncio.to_thread(
        ops.generate_op, ctx.cfg, ctx.reg, inputs["doc"], cfg.suite_id,
        ctx.clients.get("generator"), progress, cfg.cases_per_task)
    ctx.emit("node_progress", {"message": f"draft suite {suite.suite_id} "
                                          f"v{suite.version}: {len(suite.test_ids)} cases"})
    return {"suite": {"suite_id": suite.suite_id, "version": suite.version,
                      "approved": False}}


async def _run_human_gate(ctx: NodeContext, cfg: HumanGateConfig,
                          inputs: dict) -> dict:
    ref = inputs["suite"]
    suite, _ = ctx.reg.get_suite(ref["suite_id"], ref["version"])
    if not suite.approved:
        await ctx.wait_for_approval(ref["suite_id"], ref["version"])
        suite, _ = ctx.reg.get_suite(ref["suite_id"], ref["version"])
        if not suite.approved:
            raise RuntimeError("gate released but suite still unapproved")
    return {"suite": {**ref, "approved": True}}


async def _run_agent(ctx: NodeContext, cfg: AgentConfig, inputs: dict) -> dict:
    ref = cfg.model_dump()
    if cfg.deploy and cfg.agent_yaml_path:
        import yaml
        spec = yaml.safe_load(Path(cfg.agent_yaml_path).read_text())
        result = await asyncio.to_thread(
            ops.deploy_op, spec, "ascore-workflows", ctx.clients.get("anthropic"))
        ctx.emit("node_progress", {"message": f"{result['action']} managed agent "
                                              f"{result['agent_id']} v{result['version']}"})
        ref.update(variant="managed", managed_agent_id=result["agent_id"],
                   environment_id=result["environment_id"])
    return {"agent": ref}


async def _run_run_suite(ctx: NodeContext, cfg: RunSuiteConfig,
                         inputs: dict) -> dict:
    agent_ref = inputs["agent"]
    suite_id = cfg.suite_id or inputs["suite"]["suite_id"]
    version = cfg.version or inputs.get("suite", {}).get("version")
    adapter = ops.build_adapter(
        ctx.cfg, variant=agent_ref["variant"], agent_id=agent_ref["agent_id"],
        url=agent_ref.get("url", ""),
        managed_agent_id=agent_ref.get("managed_agent_id", ""),
        environment_id=agent_ref.get("environment_id", ""),
        client=ctx.clients.get("agent"))
    from ascore.harness.runner import SuiteNotApprovedError
    try:
        suite, cases, traces = await ops.run_suite_op(
            ctx.cfg, ctx.reg, adapter, suite_id, version,
            on_progress=lambda t, d: ctx.emit("node_progress", {"event": t, **d}))
    except SuiteNotApprovedError:
        # UI-appropriate hint — canvas users approve in the UI, not the CLI
        raise RuntimeError(
            f"suite {suite_id!r} is not approved (Step 8 human gate). Wire a "
            "Human Gate node before Run Suite to approve from the canvas, or "
            "approve it under Resources → suites, then run again.")
    return {"run": {
        "suite_id": suite.suite_id, "suite_version": suite.version,
        "trace_ids": [t.trace_id for t in traces],
        "agent_id": adapter.agent_id, "agent_model": ops.agent_model_of(adapter),
        "visibility": adapter.visibility,
    }}


async def _run_score(ctx: NodeContext, cfg: ScoreConfig, inputs: dict) -> dict:
    run_ref = inputs["run"]
    _, cases = ctx.reg.get_suite(run_ref["suite_id"], run_ref["suite_version"])
    traces = [ctx.reg.get_trace(tid) for tid in run_ref["trace_ids"]]
    runs = await ops.score_op(
        ctx.cfg, ctx.reg, traces, cases, run_ref["agent_model"],
        on_progress=lambda t, d: ctx.emit("node_progress", {"event": t, **d}),
        judge_client=ctx.clients.get("judge"),
        pass_threshold=cfg.pass_threshold)
    return {"scored": {**run_ref,
                       "run_scores": [r.model_dump(mode="json") for r in runs]}}


async def _run_scorecard(ctx: NodeContext, cfg: ScorecardConfig,
                         inputs: dict) -> dict:
    scored = inputs["scored"]
    suite, cases = ctx.reg.get_suite(scored["suite_id"], scored["suite_version"])
    rubric = ctx.reg.get_rubric(cases[0].rubric_id)
    runs = [RunScore.model_validate(r) for r in scored["run_scores"]]
    sc = ops.aggregate_op(ctx.reg, agent_id=scored["agent_id"], suite=suite,
                          rubric=rubric, runs=runs,
                          visibility=scored["visibility"])
    return {"scorecard": {"scorecard_id": sc.scorecard_id,
                          "task_success_rate": sc.task_success_rate,
                          "mean_cost_usd": sc.mean_cost_usd}}


async def _run_report(ctx: NodeContext, cfg: ReportConfig, inputs: dict) -> dict:
    md = await asyncio.to_thread(
        ops.report_op, ctx.reg, inputs["scorecard"]["scorecard_id"])
    if cfg.out_path:
        Path(cfg.out_path).write_text(md)
    return {"markdown": md}


async def _run_monitor(ctx: NodeContext, cfg: MonitorConfig, inputs: dict) -> dict:
    """Drift check against the wired scorecard baseline using stored live
    scores — no LLM call; live judging happens in the ingest path."""
    baseline_ref = inputs.get("scorecard")
    agent_id = cfg.agent_id or (baseline_ref or {}).get("agent_id", "")
    threshold = ctx.cfg.get("live", {}).get("drift_threshold", 0.15)
    baseline_means: dict[str, float] = {}
    if baseline_ref:
        sc = ctx.reg.get_scorecard(baseline_ref["scorecard_id"])
        agent_id = agent_id or sc.agent_id
        baseline_means = sc.per_criterion_means
    live_means, drifted = {}, []
    for cid, base in baseline_means.items():
        window = ctx.reg.live_scores(agent_id, cid, cfg.window)
        if window:
            live_means[cid] = sum(window) / len(window)
            if base - live_means[cid] > threshold:
                drifted.append(cid)
    return {"drift": {"agent_id": agent_id, "live_means": live_means,
                      "baseline_means": baseline_means, "drifted": drifted,
                      "reeval_requests": ctx.reg.reeval_requests(agent_id)}}


NODE_TYPES: dict[str, NodeSpec] = {s.type: s for s in [
    NodeSpec("business_doc", "Business Doc", "input", BusinessDocConfig,
             {}, {"doc": "doc"}, _run_business_doc,
             "A business requirements document — pasted text or an upload."),
    NodeSpec("generator", "Benchmark Generator", "benchmark", GeneratorConfig,
             {"doc": "doc"}, {"suite": "suite_ref"}, _run_generator,
             "LLM pipeline: doc → draft test suite (requires human approval)."),
    NodeSpec("human_gate", "Human Gate", "benchmark", HumanGateConfig,
             {"suite": "suite_ref"}, {"suite": "suite_ref"}, _run_human_gate,
             "Pauses the workflow until the suite is reviewed and approved."),
    NodeSpec("agent", "Agent Under Test", "agents", AgentConfig,
             {}, {"agent": "agent_ref"}, _run_agent,
             "Reference, black-box HTTP, or Managed Agent (optionally deployed "
             "from a workflow YAML)."),
    NodeSpec("run_suite", "Run Suite", "evaluation", RunSuiteConfig,
             {"suite": "suite_ref", "agent": "agent_ref"}, {"run": "run_ref"},
             _run_run_suite,
             "Harness: every case against the agent, all traces persisted."),
    NodeSpec("score", "Score", "evaluation", ScoreConfig,
             {"run": "run_ref"}, {"scored": "scored_run"}, _run_score,
             "Deterministic checks + tiered LLM judge per criterion."),
    NodeSpec("scorecard", "Scorecard", "evaluation", ScorecardConfig,
             {"scored": "scored_run"}, {"scorecard": "scorecard_ref"},
             _run_scorecard, "Aggregate run scores into an immutable scorecard."),
    NodeSpec("report", "Report", "delivery", ReportConfig,
             {"scorecard": "scorecard_ref"}, {"markdown": "markdown"},
             _run_report, "Client-ready Markdown report with regression diff."),
    NodeSpec("monitor", "Live Monitor", "delivery", MonitorConfig,
             {"scorecard": "scorecard_ref"}, {"drift": "drift_status"},
             _run_monitor, "Drift status of live traffic vs the batch baseline."),
]}
