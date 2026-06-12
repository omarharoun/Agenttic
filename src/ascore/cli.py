"""ascore CLI — the operator surface (SPEC.md `CLI surface`).

Requires ANTHROPIC_API_KEY in the environment for commands that call models
(generate, run with judge criteria, monitor with sampling).
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ascore.config import load_config
from ascore.adapters.anthropic_simple import AnthropicSimpleAgent
from ascore.adapters.blackbox_http import BlackBoxHTTPAgent
from ascore.adapters.managed_agent import ManagedAgentAdapter
from ascore.generator.pipeline import BenchmarkGenerator
from ascore.harness.runner import HarnessConfig, run_suite
from ascore.registry.sqlite_store import Registry
from ascore.reporting.scorecard_report import render_markdown
from ascore.schema.scorecard import Scorecard
from ascore.scoring.calibration import calibration_report, load_labels
from ascore.scoring.engine import score_run
from ascore.scoring.judge import make_judge

app = typer.Typer(help="Agentic scoring & benchmarking platform")
console = Console()


def _ctx(config_path: str = "config.yaml"):
    cfg = load_config(config_path)
    return cfg, Registry(cfg["paths"]["registry_db"])


@app.command()
def generate(business_doc: Path, suite_id: str, config: str = "config.yaml"):
    """Draft a test suite from a business document (requires human approval)."""
    cfg, reg = _ctx(config)
    gen = BenchmarkGenerator(model=cfg["models"]["generator"])
    suite = gen.generate_suite(business_doc.read_text(), suite_id=suite_id,
                               registry=reg, review_dir=cfg["paths"]["review_dir"])
    console.print(f"[yellow]DRAFT[/] suite {suite.suite_id} v{suite.version} "
                  f"({len(suite.test_ids)} cases). Review "
                  f"{cfg['paths']['review_dir']}/{suite_id}.md then run "
                  f"`ascore approve {suite_id}`.")


@app.command()
def approve(suite_id: str, version: int = 1, config: str = "config.yaml"):
    """Human gate: mark a reviewed suite as runnable."""
    _, reg = _ctx(config)
    reg.approve_suite(suite_id, version)
    console.print(f"[green]Approved[/] suite {suite_id} v{version}.")


def _run_and_score(cfg, reg, adapter, suite_id, version=None) -> Scorecard:
    suite, cases = reg.get_suite(suite_id, version)
    h = cfg["harness"]
    traces = asyncio.run(run_suite(
        adapter, suite, cases, reg,
        HarnessConfig(timeout_seconds=h["timeout_seconds"],
                      max_parallel=h["max_parallel"],
                      transport_retries=h["transport_retries"])))
    rubric = reg.get_rubric(cases[0].rubric_id)
    # Black-box adapters expose no model; the tiered (advisor) judge always
    # applies to them. Glass-box adapters report theirs so Hard Rule 4 can
    # force the fallback to a plain judge_strong judge.
    agent_model = getattr(adapter, "model", None) or f"blackbox:{adapter.agent_id}"
    judge = make_judge(cfg, agent_model)
    runs = [score_run(t, c, reg.get_rubric(c.rubric_id), judge)
            for t, c in zip(traces, cases)]
    sc = Scorecard.aggregate(
        scorecard_id=uuid.uuid4().hex[:12], agent_id=adapter.agent_id,
        suite_id=suite.suite_id, suite_version=suite.version,
        rubric_id=rubric.rubric_id, rubric_version=rubric.version,
        run_scores=runs, visibility_tier=adapter.visibility)
    reg.save_scorecard(sc)
    return sc


@app.command()
def run(agent: str, suite: str, url: str = "",
        managed_agent_id: str = "", environment_id: str = "",
        config: str = "config.yaml"):
    """Run a suite against an agent: the reference agent, --url for
    black-box HTTP, or --managed-agent-id/--environment-id for a deployed
    Anthropic Managed Agent (see `ascore deploy`)."""
    cfg, reg = _ctx(config)
    if managed_agent_id:
        if not environment_id:
            environment_id = cfg.get("managed", {}).get("environment_id", "")
        if not environment_id:
            raise typer.BadParameter(
                "--environment-id required (or set managed.environment_id in config)")
        adapter = ManagedAgentAdapter(
            managed_agent_id=managed_agent_id, environment_id=environment_id,
            agent_id=agent)
    elif url:
        adapter = BlackBoxHTTPAgent(agent_id=agent, url=url)
    else:
        adapter = AnthropicSimpleAgent(model=cfg["models"]["agent_default"],
                                       kb_path="kb.json", agent_id=agent,
                                       max_steps=cfg["harness"]["max_steps"])
    sc = _run_and_score(cfg, reg, adapter, suite)
    console.print(f"Scorecard [bold]{sc.scorecard_id}[/]: success "
                  f"{sc.task_success_rate:.0%}, mean cost ${sc.mean_cost_usd:.4f}")


@app.command()
def deploy(workflow: Path, env_name: str = "ascore-workflows",
           config: str = "config.yaml"):
    """Deploy a business-workflow agent to Anthropic Managed Agents (beta).

    WORKFLOW is a version-controlled YAML: name, model, system, optional
    tools/skills. The agent is created ONCE and versioned server-side —
    re-deploying the same name updates it (new immutable version) instead of
    creating a duplicate. The environment is reused by name across deploys.
    """
    import anthropic
    import yaml

    spec = yaml.safe_load(workflow.read_text())
    client = anthropic.Anthropic()

    env = next((e for e in client.beta.environments.list()
                if getattr(e, "name", "") == env_name), None)
    if env is None:
        env = client.beta.environments.create(
            name=env_name,
            config={"type": "cloud", "networking": {"type": "unrestricted"}})

    existing = next((a for a in client.beta.agents.list()
                     if getattr(a, "name", "") == spec["name"]), None)
    body = dict(
        name=spec["name"],
        model=spec["model"],
        system=spec.get("system", ""),
        tools=spec.get("tools", [{"type": "agent_toolset_20260401"}]),
    )
    if spec.get("skills"):
        body["skills"] = spec["skills"]
    if existing is None:
        agent = client.beta.agents.create(**body)
        action = "Created"
    else:
        agent = client.beta.agents.update(existing.id, **body)
        action = "Updated"

    console.print(f"[green]{action}[/] agent [bold]{agent.id}[/] "
                  f"v{agent.version} ({spec['name']}) in env {env.id}")
    console.print(
        f"Run a suite against it:\n  ascore run --agent {spec['name']} "
        f"--suite <suite_id> --managed-agent-id {agent.id} "
        f"--environment-id {env.id}")


@app.command()
def calibrate(suite_id: str, config: str = "config.yaml"):
    """Compare judge scores against human labels (calibration/{suite_id}.csv)."""
    from sqlmodel import Session, select
    from ascore.registry.sqlite_store import ScorecardRow

    cfg, reg = _ctx(config)
    labels = load_labels(Path(cfg["paths"]["calibration_dir"]) / f"{suite_id}.csv")

    # criterion scales from the suite's rubrics
    _, cases = reg.get_suite(suite_id)
    scales: dict[str, str] = {}
    for rid in {c.rubric_id for c in cases}:
        for crit in reg.get_rubric(rid).criteria:
            scales[crit.criterion_id] = crit.scale

    # every judge score recorded for this suite
    collected: list[tuple[str, str, float]] = []
    with Session(reg.engine) as s:
        rows = s.exec(select(ScorecardRow).where(
            ScorecardRow.suite_id == suite_id)).all()
    for row in rows:
        sc = Scorecard.model_validate_json(row.payload)
        for r in sc.run_scores:
            for cs in r.criterion_scores:
                if cs.scorer == "judge":
                    collected.append((r.trace_id, cs.criterion_id, cs.score))

    report = calibration_report(collected, labels, scales,
                                threshold=cfg["scoring"]["calibration_threshold"])
    table = Table("criterion", "n", "agreement", "status")
    for cal in report.values():
        table.add_row(cal.criterion_id, str(cal.n), f"{cal.agreement:.2f}",
                      "[green]calibrated[/]" if cal.calibrated
                      else "[red]UNCALIBRATED[/]")
    console.print(table)


@app.command()
def regress(agent: str, config: str = "config.yaml"):
    """Re-run every suite this agent was scored on; diff against prior results."""
    cfg, reg = _ctx(config)
    for suite_id in reg.suites_scored_for(agent):
        history = reg.scorecards_for(agent, suite_id)
        previous = history[-1]
        adapter = AnthropicSimpleAgent(model=cfg["models"]["agent_default"],
                                       kb_path="kb.json", agent_id=agent)
        sc = _run_and_score(cfg, reg, adapter, suite_id)
        delta = sc.task_success_rate - previous.task_success_rate
        colour = "green" if delta >= 0 else "red"
        console.print(f"{suite_id}: {previous.task_success_rate:.0%} → "
                      f"[{colour}]{sc.task_success_rate:.0%}[/]")


@app.command()
def report(scorecard_id: str, out: Path = Path("report.md"),
           config: str = "config.yaml"):
    """Render a scorecard to a client-ready Markdown report."""
    cfg, reg = _ctx(config)
    sc = reg.get_scorecard(scorecard_id)
    rubric = reg.get_rubric(sc.rubric_id, sc.rubric_version)
    history = reg.scorecards_for(sc.agent_id, sc.suite_id)
    previous = next((h for h in reversed(history)
                     if h.scorecard_id != sc.scorecard_id), None)
    out.write_text(render_markdown(sc, rubric, previous))
    console.print(f"Wrote {out}")


@app.command()
def monitor(action: str, agent: str = "", config: str = "config.yaml"):
    """Live path: `monitor status --agent X` prints drift state."""
    cfg, reg = _ctx(config)
    if action != "status":
        console.print("ingest is wired programmatically via LiveMonitor.ingest()")
        raise typer.Exit(1)
    for req in reg.reeval_requests(agent):
        console.print(f"[red]RE-EVAL[/] {req}")
    console.print("No drift on record." if not reg.reeval_requests(agent) else "")


if __name__ == "__main__":
    app()
