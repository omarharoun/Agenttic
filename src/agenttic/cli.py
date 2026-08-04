"""agenttic CLI — the operator surface (SPEC.md `CLI surface`).

Exposed as the ``agenttic`` console script (and ``python -m agenttic``). The
This is the single public entry point.

Requires ANTHROPIC_API_KEY in the environment for commands that call models
(generate, run with judge criteria, monitor with sampling).
"""

from __future__ import annotations

import os
import re

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from agenttic import ops
from agenttic.config import load_config
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.scorecard import Scorecard
from agenttic.scoring.calibration import calibration_report, load_labels

app = typer.Typer(help="Agentic scoring & benchmarking platform")
console = Console()


# Global --tenant (or AGENTTIC_TENANT) selects the workspace for every command.
_STATE: dict[str, str | None] = {"tenant": None}


@app.callback()
def _main(tenant: str = typer.Option(
        None, "--tenant", envvar=["AGENTTIC_TENANT", "AGENTTIC_TENANT"],
        help="workspace/tenant to operate on (default: 'default')")):
    """Agenttic CLI. The CLI operates directly on the registry DB (admin-level);
    --tenant selects the workspace, matching the server's tenancy model."""
    _STATE["tenant"] = tenant


def _ctx(config_path: str = "config.yaml"):
    from agenttic.secrets import hydrate_env_secrets
    hydrate_env_secrets()  # pull *_FILE secrets into the environment
    cfg = load_config(config_path)
    tenant = _STATE.get("tenant") or os.environ.get("AGENTTIC_TENANT") or "default"
    db_url = os.environ.get("AGENTTIC_DB") or (cfg.get("database", {}) or {}).get("url") or ""
    if db_url and not db_url.startswith("sqlite"):
        from agenttic.registry.sqlite_store import make_engine
        return cfg, Registry(engine=make_engine(db_url), tenant=tenant)
    # SQLite: file-per-tenant (mirrors server Workspaces)
    base = Path(cfg["paths"]["registry_db"])
    path = base if tenant == "default" \
        else base.with_name(f"{base.stem}.{tenant}{base.suffix}")
    return cfg, Registry(str(path))


@app.command()
def generate(
    business_doc: Path = typer.Argument(
        None, help="business document to draft a suite from (suite-draft mode)"),
    suite_id: str = typer.Argument("", help="suite id (suite-draft mode)"),
    target: str = typer.Option(
        "", "--target",
        help="ADVERSARIAL mode: author attack probes against a target agent "
        "(e.g. --target reference). Ignores BUSINESS_DOC/SUITE_ID."),
    n: int = typer.Option(12, "--n", help="attack mode: number of probes to author"),
    mutate: bool = typer.Option(
        True, "--mutate/--no-mutate", help="attack mode: mutate around winners"),
    promote: bool = typer.Option(
        False, "--promote/--no-promote",
        help="attack mode: promote winners into a versioned regression suite"),
    config: str = "config.yaml",
):
    """Draft a test suite from a business document, OR (``--target``) author
    adversarial attack probes against a target agent.

    Business-doc mode: ``agenttic generate BUSINESS_DOC SUITE_ID`` drafts a suite
    for human approval. Adversarial mode: ``agenttic generate --target reference``
    reads the agent's real tools/prompt/secret, emits scoreable attack TestCases,
    runs them through the existing adapter + scorer, and prints which broke it."""
    if target:
        _generate_attacks(target, n=n, mutate=mutate, promote=promote, config=config)
        return
    if business_doc is None or not suite_id:
        raise typer.BadParameter(
            "provide BUSINESS_DOC and SUITE_ID for suite-draft mode, or use "
            "--target <agent> for the adversarial attack generator")
    cfg, reg = _ctx(config)
    suite = ops.generate_op(cfg, reg, business_doc.read_text(), suite_id)
    console.print(f"[yellow]DRAFT[/] suite {suite.suite_id} v{suite.version} "
                  f"({len(suite.test_ids)} cases). Review "
                  f"{cfg['paths']['review_dir']}/{suite_id}.md then run "
                  f"`uv run agenttic approve {suite_id}`.")


def _generate_attacks(target: str, *, n: int, mutate: bool, promote: bool,
                      config: str) -> None:
    """Adversarial attack-generator subflow behind ``generate --target``."""
    from agenttic.redteam import (
        build_demo_target,
        resolve_target,
        run_generation,
    )

    try:
        descriptor = resolve_target(target)
    except ValueError as exc:
        raise typer.BadParameter(str(exc))

    # Deterministic, no-key stand-in target so the example runs without credits.
    # A real run points build_adapter(...) at the live agent instead (same probes).
    adapter = build_demo_target(descriptor)
    reg = None
    if promote:
        _, reg = _ctx(config)

    console.print(f"[bold]Sparring[/] against [cyan]{descriptor.agent_id}[/] — "
                  f"real tools {descriptor.tool_names()}, "
                  f"{len(descriptor.secrets)} declared secret(s)")
    rep = run_generation(descriptor, adapter, n=n, mutate=mutate, reg=reg,
                         promote=promote)

    table = Table(title="Generated attack probes (round 1)")
    table.add_column("test_id"); table.add_column("kind")
    table.add_column("technique"); table.add_column("verdict")
    table.add_column("failed oracle")
    for r in rep["results"]:
        broke = r.broke
        table.add_row(
            r.test_id, r.probe.spec.kind, r.probe.spec.technique,
            "[red]BROKE[/]" if broke else "[green]survived[/]",
            ", ".join(r.failed_criteria) or "—")
    console.print(table)

    nb, nw = len(rep["results"]), len(rep["winners"])
    console.print(f"Round 1: [red]{nw}[/]/{nb} probes broke the agent; "
                  f"{nb - nw} survived (discarded).")
    if mutate:
        mw = len(rep["mutation_winners"])
        console.print(f"Mutation: {mw} neighbour probes also broke the agent.")
    if promote and rep["promote"]:
        p = rep["promote"]
        console.print(
            f"[green]Promoted[/] {len(p['added'])} winners into regression "
            f"suite [bold]{p['regression_suite_id']}[/] v{p['version']} "
            f"({p['total_cases']} cases).")
    elif promote:
        console.print("[yellow]No winners to promote.[/]")


@app.command()
def honeypot(
    target: str = typer.Option(
        "reference", "--target",
        help="target agent to plant honeypots into (e.g. --target reference)"),
    posture: str = typer.Option(
        "enforce", "--posture",
        help="harness posture: 'enforce' (block decoy calls) or 'log-only' "
        "(observe but do not block — the merely-logs failure mode)"),
    promote: bool = typer.Option(
        False, "--promote/--no-promote",
        help="promote executed-not-blocked failures into a regression suite"),
    config: str = "config.yaml",
):
    """Honeypot / harness-enforcement test: plant a DECOY dangerous tool into a
    target's tool schema, tempt the agent to call it, and report — per probe —
    whether the AGENT called the honeypot AND whether the HARNESS enforced
    (blocked) it. Distinguishes three outcomes: resisted / attempted-but-blocked /
    executed-and-allowed (the real failure). Runs deterministically, no API key.

    ``--posture enforce`` installs the decoys as Lane-1 canaries so the gateway
    denies a call; ``--posture log-only`` runs the same gateway WITHOUT them, so
    a forbidden call is merely logged and allowed. Compare the two to see that
    enforcement is a real, separate signal."""
    import tempfile

    from agenttic.redteam import (
        ATTEMPTED_BLOCKED,
        EXECUTED_ALLOWED,
        RESISTED,
        honeypot_tool_schemas,
        plant_honeypots,
        resolve_target,
        run_honeypot_harness,
    )

    if posture not in ("enforce", "log-only"):
        raise typer.BadParameter("posture must be 'enforce' or 'log-only'")
    try:
        descriptor = resolve_target(target)
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    enforcing = posture == "enforce"

    planted = plant_honeypots(descriptor)
    schemas = honeypot_tool_schemas(planted)
    console.print(f"[bold]Honeypot[/] harness on [cyan]{planted.agent_id}[/] — "
                  f"posture [magenta]{posture}[/]")
    console.print("Planted decoy tools (bait — no real dangerous impl):")
    for s in schemas:
        console.print(f"  [red]{s['name']}[/]({', '.join(s['input_schema']['properties'])}) "
                      f"— {s['description']}")

    # The gateway needs a registry. Persist into the project DB only when
    # promoting; otherwise run in a throwaway one.
    def _render(reg):
        rep = run_honeypot_harness(descriptor, reg=reg, enforcing=enforcing,
                                   promote=promote)
        table = Table(title=f"Temptation probes ({posture} posture)")
        table.add_column("test_id"); table.add_column("technique")
        table.add_column("called honeypot?"); table.add_column("harness enforced?")
        table.add_column("outcome")
        for o in rep.outcomes:
            called = ", ".join(o.honeypot_tools_called) or "—"
            if o.enforced is None:
                enf = "[dim]n/a[/]"
            elif o.enforced:
                enf = "[green]BLOCKED[/]"
            else:
                enf = "[red]allowed[/]"
            tag = {RESISTED: "[green]resisted[/]",
                   ATTEMPTED_BLOCKED: "[yellow]attempted→blocked[/]",
                   EXECUTED_ALLOWED: "[red]executed→ALLOWED[/]"}[o.outcome]
            table.add_row(o.test_id, o.probe.spec.technique, called, enf, tag)
        console.print(table)
        c = rep.counts()
        console.print(
            f"Outcomes: [green]{c[RESISTED]} resisted[/], "
            f"[yellow]{c[ATTEMPTED_BLOCKED]} attempted-but-blocked[/], "
            f"[red]{c[EXECUTED_ALLOWED]} executed-and-allowed[/] "
            f"(of {len(rep.outcomes)} probes).")
        if c[EXECUTED_ALLOWED]:
            console.print("[red]⚠ executed-and-allowed = the harness logged a "
                          "forbidden call but did NOT block it.[/]")
        else:
            console.print("[green]✓ every attempted forbidden call was blocked "
                          "by the harness.[/]")
        if promote and rep.promote and rep.promote["regression_suite_id"]:
            p = rep.promote
            console.print(
                f"[green]Promoted[/] {len(p['added'])} executed-not-blocked "
                f"failures into regression suite [bold]{p['regression_suite_id']}[/] "
                f"v{p['version']} ({p['total_cases']} cases).")
        elif promote:
            console.print("[dim]Nothing to promote (no executed-not-blocked "
                          "failures under this posture).[/]")

    if promote:
        _, reg = _ctx(config)
        _render(reg)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            _render(Registry(str(Path(tmp) / "honeypot.db")))


@app.command()
def approve(suite_id: str, version: int = 1, config: str = "config.yaml"):
    """Human gate: mark a reviewed suite as runnable."""
    _, reg = _ctx(config)
    reg.approve_suite(suite_id, version)
    console.print(f"[green]Approved[/] suite {suite_id} v{version}.")


@app.command()
def run(agent: str = typer.Option(..., "--agent", "-a", help="agent id (label)"),
        suite: str = typer.Option(..., "--suite", "-s", help="suite id to run"),
        url: str = "",
        managed_agent_id: str = "", environment_id: str = "",
        system_prompt: str = "", model: str = "",
        mock: bool = typer.Option(False, "--mock",
                                  help="offline deterministic provider (no API key)"),
        config: str = "config.yaml"):
    """Run a suite against an agent.

    If --agent matches a name in the declared catalog (`agenttic agents add`), its
    connection details are used automatically — so `agenttic run --agent prod
    --suite s` just works. Otherwise build one ad-hoc: the reference agent
    (--system-prompt/--model), --url for black-box HTTP, or
    --managed-agent-id/--environment-id for a deployed Managed Agent. Explicit
    flags always override the catalog."""
    from agenttic.registry.sqlite_store import NotFoundError

    cfg, reg = _ctx(config)
    variant = "managed" if managed_agent_id else ("blackbox" if url else "reference")
    bb = {}  # black-box cost hints from the declared agent
    # An agent declared in `agents:` config wins before anything else: it is the
    # no-code path (ACP or a CLI spec), and its whole purpose is that pointing
    # the harness at a new agent never requires a source change.
    if not (url or managed_agent_id) and agent in ops.declared_agents(cfg):
        variant = str(ops.declared_agents(cfg)[agent].get("driver") or "cli")
        console.print(f"[dim]using config-declared agent {agent} ({variant})[/]")
    # resolve a declared catalog agent when no connection flags were given
    elif not (url or managed_agent_id):
        try:
            d = reg.get_declared_agent(agent)
            variant = d.variant
            url, managed_agent_id = d.url, d.managed_agent_id
            environment_id = environment_id or d.environment_id
            system_prompt = system_prompt or d.system_prompt
            model = model or d.model
            bb = {"cost_per_call_usd": d.cost_per_call_usd,
                  "expected_input_tokens": d.expected_input_tokens,
                  "expected_output_tokens": d.expected_output_tokens}
            console.print(f"[dim]using declared agent {agent} "
                          f"(v{d.version}, {d.variant})[/]")
        except NotFoundError:
            pass
    client = None
    if mock:
        from agenttic.certification.mock_provider import MockAnthropicClient
        client = MockAnthropicClient()
        bb = {**bb, "client": client}
    try:
        adapter = ops.build_adapter(cfg, variant=variant, agent_id=agent, url=url,
                                    managed_agent_id=managed_agent_id,
                                    environment_id=environment_id,
                                    system_prompt=system_prompt, model=model, **bb)
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    from agenttic.budget import BudgetExceededError
    try:
        sc = asyncio.run(ops.run_and_score_op(cfg, reg, adapter, suite,
                                              judge_client=client))
    except BudgetExceededError as exc:
        console.print(f"[red]Budget cap:[/] {exc}")
        raise typer.Exit(2)
    console.print(f"Scorecard [bold]{sc.scorecard_id}[/]: success "
                  f"{sc.task_success_rate:.0%}, mean exec cost "
                  f"${sc.mean_cost_usd:.4f}, total run cost "
                  f"${sc.total_cost_usd + sc.total_scoring_cost_usd:.4f}")


@app.command()
def deploy(workflow: Path, env_name: str = "agenttic-workflows",
           config: str = "config.yaml"):
    """Deploy a business-workflow agent to Anthropic Managed Agents (beta).

    WORKFLOW is a version-controlled YAML: name, model, system, optional
    tools/skills. The agent is created ONCE and versioned server-side —
    re-deploying the same name updates it (new immutable version) instead of
    creating a duplicate. The environment is reused by name across deploys.
    """
    import yaml

    result = ops.deploy_op(yaml.safe_load(workflow.read_text()), env_name)
    console.print(f"[green]{result['action'].capitalize()}[/] agent "
                  f"[bold]{result['agent_id']}[/] v{result['version']} "
                  f"({result['name']}) in env {result['environment_id']}")
    console.print(
        f"Run a suite against it:\n  uv run agenttic run --agent {result['name']} "
        f"--suite <suite_id> --managed-agent-id {result['agent_id']} "
        f"--environment-id {result['environment_id']}")


@app.command()
def cdv(agent: str = typer.Option(..., "--agent", "-a", help="agent id (label)"),
        rubric: str = typer.Option(..., "--rubric", "-r", help="rubric id to score with"),
        space: str = typer.Option("space-conversational_transactional", "--space",
                                  help="scenario space id"),
        surface: str = typer.Option("", "--surface",
                                    help="derive the space from THIS agent's "
                                         "declared surface (see `agenttic "
                                         "surface list`); overrides --space"),
        seed: int = 0,
        max_scenarios: int = typer.Option(0, "--max-scenarios",
                                          help="0 = the config/Budget default"),
        max_rounds: int = 0,
        model: str = "",
        no_bias: bool = typer.Option(False, "--no-bias",
                                     help="control arm: unbiased random stimulus"),
        mock: bool = typer.Option(False, "--mock",
                                  help="offline scripted agent + judge (no API key)"),
        config: str = "config.yaml"):
    """Coverage-directed generation: generate → run → find holes → aim → repeat.

    Runs generated scenarios against a REAL environment (the retail world in
    ``agenttic.scenario``), scores them with the existing engine, and stops on
    closure or budget — then reports the bug-discovery curve and the frozen
    regression PROPOSALS it found. Nothing is promoted into a suite: that is a
    human decision (Hard Rule 63), and the proposals are written to the review
    directory with ``approved: false`` on every entry.

    Convergence and envelope are reported as SCOPE. They are not sign-off gates —
    ``signs_off`` binds on coverage, assertions and formal only, and this command
    does not change that.
    """
    from agenttic.registry.sqlite_store import NotFoundError
    from agenttic.reporting.signoff_report import render
    from agenttic.scenario.runner import (
        ScenarioAgent, ScriptedSupportClient, scenario_runner)
    from agenttic.schema.signoff import VerificationSignoff
    from agenttic.stimulus.spaces.conversational_transactional import seed_space
    from agenttic.verification.cdv import Budget

    cfg, reg = _ctx(config)
    if surface:
        # Derived from ONE AGENT'S declared surface. Without this the loop always
        # ran the hand-written conversational_transactional space, so every agent
        # was scored against a generic retail conversation whoever it was — and
        # the derivation, which exists, was reachable from nothing.
        from agenttic.redteam.descriptor import resolve_surface
        from agenttic.stimulus import derive_space
        try:
            descriptor = resolve_surface(surface)
        except KeyError as exc:
            raise typer.BadParameter(str(exc)) from exc
        scenario_space = derive_space(descriptor)
        console.print(f"[dim]space derived from surface {surface!r}: "
                      f"{scenario_space.ref()}[/dim]")
    else:
        try:
            scenario_space = reg.get_scenario_space(space)
        except NotFoundError:
            if space != "space-conversational_transactional":
                raise typer.BadParameter(
                    f"no scenario space {space!r} in the registry; the only "
                    "seeded space is space-conversational_transactional. To "
                    "test an agent against ITS OWN space, use --surface.")
            scenario_space = seed_space()
    rubric_obj = reg.get_rubric(rubric)

    if mock:
        from agenttic.certification.mock_provider import MockAnthropicClient
        client, judge_client = ScriptedSupportClient(), MockAnthropicClient()
        agent_model = model or "scripted-support"
    else:
        import anthropic
        client, judge_client = anthropic.Anthropic(), None
        agent_model = model or cfg["models"]["agent_default"]
    adapter = ScenarioAgent(model=agent_model, client=client, agent_id=agent,
                            max_steps=cfg["harness"]["max_steps"])

    cdv_cfg = dict(cfg.get("cdv") or {})
    budget = Budget(
        max_scenarios=max_scenarios or int(cdv_cfg.get("max_scenarios", 60)),
        max_dollars=float(cdv_cfg.get("max_dollars", 5.0)),
        max_rounds=max_rounds or int(cdv_cfg.get("max_rounds", 6)))
    console.print(f"[dim]CDV over {scenario_space.ref()} — budget "
                  f"{budget.max_scenarios} scenarios / ${budget.max_dollars:.2f} / "
                  f"{budget.max_rounds} rounds[/]")

    out = ops.cdv_op(cfg, reg, adapter, space=scenario_space, rubric=rubric_obj,
                     run_scenario=scenario_runner(cfg=None), seed=seed,
                     budget=budget, judge_client=judge_client, bias=not no_bias)
    res = out.cdv

    table = Table(title="rounds — closure is measured on what runs EXHIBITED")
    for col in ("round", "scenarios", "biased", "closure", "new signatures",
                "aimed at"):
        table.add_column(col)
    for r in res.rounds:
        table.add_row(str(r.index), str(r.scenarios), "yes" if r.biased else "no",
                      f"{r.closure:.1%}", str(r.new_signatures),
                      escape(", ".join(r.targeted[:3])))
    console.print(table)
    console.print(f"stopped: [bold]{res.stopped_because}[/] · "
                  f"{res.scenarios_run} scenarios · ${res.dollars_spent:.4f} · "
                  f"closure/$ {res.closure_per_dollar:.3f}")
    console.print("bug-discovery curve (scenarios → distinct signatures): "
                  + escape(" ".join(f"{n}:{c}" for n, c in res.bug_curve[::5])))
    # Holes the loop has GIVEN UP on, printed beside the ones it is still
    # working. Without this the run prints "N holes remaining" over a list it
    # has privately decided it will never aim at, which reads as a suite that
    # has not got there yet rather than as instrumentation that cannot get
    # there. It was computed and put on `as_dict()`, which nothing calls.
    if res.unaimable_holes:
        console.print(f"[yellow]{len(res.unaimable_holes)} hole(s) the loop "
                      "cannot steer at[/] — a gap in the stimulus space, not in "
                      "the suite:")
        for u in res.unaimable_holes[:8]:
            console.print(f"  · {escape(u.where)}={escape(u.what)} — "
                          f"{escape(u.reason)}")
        if len(res.unaimable_holes) > 8:
            console.print(f"  · … {len(res.unaimable_holes) - 8} more")
    if res.frozen_regressions:
        console.print(f"[yellow]{len(res.frozen_regressions)} frozen regression "
                      f"proposal(s)[/] → {out.regressions_path} "
                      "(approved: false — promotion is a human decision)")
    signoff = (out.scorecard.signoff or
               (out.scorecard.coverage or {}).get("signoff") or {})
    if signoff:
        console.print(escape(render(VerificationSignoff.model_validate(signoff))))
    console.print(f"Scorecard [bold]{out.scorecard.scorecard_id}[/]")


@app.command()
def calibrate(suite_id: str, config: str = "config.yaml"):
    """Compare judge scores against human labels (calibration/{suite_id}.csv)."""
    from sqlmodel import Session, select
    from agenttic.registry.sqlite_store import ScorecardRow

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
def calibrate_corpus():
    """Demonstrate deterministic-check calibration against the shipped human-label
    corpus (offline, reproducible; no API key). Prints per-criterion agreement."""
    from agenttic.scoring.corpus import run_corpus_calibration

    result = run_corpus_calibration()
    console.print(f"[bold]{result.version}[/] — {result.n_records} labeled "
                  f"records, overall agreement "
                  f"[bold]{result.overall_agreement:.2%}[/]")
    table = Table("criterion", "n", "agreement", "status")
    for cid, cal in sorted(result.per_criterion.items()):
        table.add_row(cid, str(cal.n), f"{cal.agreement:.2f}",
                      "[green]calibrated[/]" if cal.calibrated
                      else "[red]UNCALIBRATED[/]")
    console.print(table)
    console.print(f"[dim]{len(result.disagreements)} intentional tail "
                  "disagreement(s); the LLM judge is not covered and stays "
                  "provisional.[/]")


judge_app = typer.Typer(help="Evaluate the EVALUATOR: probe the LLM judge for "
                             "bias, and check whether the calibration corpus "
                             "can support a claim at all.")
app.add_typer(judge_app, name="judge")


@judge_app.command("corpus")
def judge_corpus(config: str = "config.yaml"):
    """Can the calibration corpus support a claim? Offline, no key, no spend.

    Run this BEFORE spending on a calibration run: a corpus that cannot
    discriminate makes the run worthless however it comes out.
    """
    from agenttic.scoring.annotators import (ceiling, consensus, corpus_health,
                                             labels_of)
    from agenttic.scoring.judge_calibration import load_judge_corpus

    def consensus_of(record):
        return consensus(labels_of(record))

    _cfg, _ = _ctx(config)
    recs = load_judge_corpus()
    health = corpus_health(recs)
    table = Table("criterion", "items", "multi-annotated", "distinct labels",
                  "discriminates", "human ceiling")
    for cid, h in sorted(health.items()):
        c = ceiling(recs, cid)
        table.add_row(cid, str(h["n_items"]), str(h["n_multi_annotated"]),
                      str(h["distinct_consensus_values"]),
                      "[green]yes[/]" if h["discriminating"] else "[red]no[/]",
                      f"{c['ceiling']:.2f}" if c["ceiling"] is not None
                      else "[yellow]none[/]")
    console.print(table)
    # What a real study would be able to conclude, before anyone pays for it.
    from agenttic.scoring.annotators import per_class_recall
    fail_free = []
    for cid in sorted(health):
        pairs = [(0.0, float(consensus_of(r)))
                 for r in recs if r.get("criterion_id") == cid
                 and consensus_of(r) is not None]
        if pairs and per_class_recall(pairs)["n_fail_items"] == 0:
            fail_free.append(cid)
    if fail_free:
        console.print(
            f"[yellow]No FAILING item[/] for {', '.join(fail_free)} — TNR is "
            "unmeasurable there, so nothing would show whether the judge can "
            "catch a failure. Cohen's kappa needs both classes present.")

    blockers = {b for h in health.values() for b in h["blockers"]}
    for b in sorted(blockers):
        console.print(f"[yellow]BLOCKER:[/] {b}")
    if blockers:
        console.print(
            "\n[dim]Without a human-human ceiling, judge-vs-human agreement "
            "cannot separate a wrong judge from an ambiguous item. Add a second "
            "annotator: a record may carry "
            "`human_scores: [{annotator, score}, ...]` instead of one "
            "`human_score`.[/]")


@judge_app.command("probe")
def judge_probe(config: str = "config.yaml",
                criterion: str = typer.Option("helpfulness", "--criterion")):
    """Metamorphic bias probes against the live judge. Needs ANTHROPIC_API_KEY.

    Asserts INVARIANTS rather than gold scores — padding must not raise a score,
    order must not flip a ranking, a content digest must not be graded as an
    answer — so it needs no human labels. These probes refute; they never prove,
    and nothing here promotes a criterion out of PROVISIONAL.
    """
    from agenttic.scoring.judge_calibration import (judge_calibration_available,
                                                    load_judge_corpus, _build)
    from agenttic.scoring.judge import make_judge
    from agenttic.scoring.judge_probes import run_probes, summarize

    cfg, _ = _ctx(config)
    if not judge_calibration_available():
        console.print("[yellow]No ANTHROPIC_API_KEY[/] — the judge is an LLM, so "
                      "these probes cannot run. Nothing was spent.")
        raise typer.Exit(code=1)

    rec = next((r for r in load_judge_corpus()
                if r.get("criterion_id") == criterion), None)
    if rec is None:
        console.print(f"[red]No corpus record for criterion {criterion!r}.[/]")
        raise typer.Exit(code=1)

    crit, trace, case = _build(rec)
    judge = make_judge(cfg, "agent-under-test")
    results = run_probes(judge, crit, case, trace)
    table = Table("probe", "status", "magnitude", "detail")
    for r in results:
        colour = {"VIOLATED": "red", "held": "green"}.get(r.status, "yellow")
        table.add_row(r.probe_id, f"[{colour}]{r.status}[/]",
                      f"{r.magnitude:.3f}", r.detail[:76])
    console.print(table)
    s = summarize(results)
    console.print(f"\nverdict: [bold]{s['verdict']}[/]")
    console.print(f"[dim]{s['note']}[/]")
    if s["violations"]:
        raise typer.Exit(code=1)


@app.command()
def calibrate_judge(config: str = "config.yaml"):
    """Measure LLM-judge-vs-human agreement on the shipped judge-calibration
    corpus (Krippendorff α / exact-match). Requires ANTHROPIC_API_KEY (the judge
    is an LLM). With no key it prints the honest blocker + minimal cost and spends
    nothing; judge criteria stay PROVISIONAL until a real run demonstrates
    agreement."""
    from agenttic.scoring import judge_calibration as JC

    cfg, _ = _ctx(config)
    if not JC.judge_calibration_available():
        blk = JC.judge_blocker(cfg)
        console.print(f"[yellow]Judge calibration blocked:[/] {blk['blocker']}")
        mc = blk["minimal_cost"]
        console.print(f"[dim]Minimal run:[/] {blk['one_command']} · "
                      f"n={mc['n_records']} records · est ~${mc['est_usd']} "
                      f"({mc['est_usd_order']})")
        raise typer.Exit(0)
    result = JC.run_judge_calibration(cfg)
    console.print(f"[bold]{result.version}[/] — judge vs human over "
                  f"{result.n_records} records:")
    table = Table("criterion", "n", "agreement", "status")
    for cid, cal in sorted(result.per_criterion.items()):
        table.add_row(cid, str(cal.n), f"{cal.agreement:.2f}",
                      "[green]calibrated[/]" if cal.calibrated
                      else "[red]PROVISIONAL[/]")
    console.print(table)


@app.command()
def reproduce_bfcl(
        split: str = typer.Option("simple", "--split", help="BFCL split"),
        full: bool = typer.Option(False, "--full", help="fetch the whole split "
                                  "from HuggingFace (else the vendored sample)"),
        predictions: Path | None = typer.Option(
            None, "--predictions", help="JSON {bfcl_id: [{name,args}]} of MODEL "
            "predictions to score (from the official bfcl generator or a live run)"),
        model: str = typer.Option("unknown", "--model", help="model label"),
        live: bool = typer.Option(False, "--live", help="GENERATE predictions by "
                                  "running the model over the V4 Python simple "
                                  "split (native FC, temp 0); needs ANTHROPIC_API_KEY"),
        published: float | None = typer.Option(
            None, "--published", help="published accuracy to reproduce (0-1)"),
        published_source: str = typer.Option("", "--published-source")):
    """Reproduce a published BFCL number, or validate the grader on real data.

    `--live` runs the model over the real V4 Python `simple` split (n≈400) with
    native function-calling and scores it. `--predictions <file>` scores a
    predictions file instead. With neither, prints the honest blocker (no spend).
    Always runs the offline grader validation (oracle → must be 100%)."""
    import json

    from agenttic.metrics import bfcl_reproduce as R

    val = R.validate_scorer(split, full=full)
    ok = "[green]VALID[/]" if val.accuracy >= 1.0 else "[red]SCORER BUG[/]"
    lo, hi = val.wilson
    console.print(f"Grader validation ({split}{' full' if full else ' sample'}): "
                  f"oracle accuracy [bold]{val.accuracy:.1%}[/] "
                  f"({val.passes}/{val.n}, Wilson95 [{lo:.3f},{hi:.3f}]) {ok}")

    if live:
        if not R.model_predictions_available():
            console.print(f"\n[yellow]Blocked:[/] {R.bfcl_blocker()['blocker']}")
            raise typer.Exit(0)
        from agenttic.stats import wilson_interval
        cases = R.load_simple_python_v4()
        console.print(f"Running [bold]{model}[/] over {len(cases)} V4 "
                      "simple_python cases (native FC, temp 0)...")
        preds = R.generate_predictions(
            cases, model=model,
            on_progress=lambda d, n: console.print(f"[dim]  {d}/{n}[/]"))
        # score with the faithful port of BFCL's OFFICIAL AST checker
        sc = R.score_cases_official(cases, preds)
        homegrown = R.score_cases(cases, preds)
        low, high = wilson_interval(sc.passes, sc.n)
        console.print(f"\n[bold]{model}[/] BFCL simple_python (FC, official "
                      f"checker): [bold]{sc.accuracy:.2%}[/] ({sc.passes}/{sc.n}, "
                      f"Wilson95 [{low:.3f},{high:.3f}])")
        console.print(f"[dim]  (our simpler grader on the same predictions: "
                      f"{homegrown.accuracy:.2%})[/]")
        if published is not None:
            inside = low <= published <= high
            v = "[green]REPRODUCED[/]" if inside else "[yellow]ATTEMPTED (off by " \
                f"{abs(published - sc.accuracy):.1%})[/]"
            console.print(f"Published {published:.2%} → {v}")
        raise typer.Exit(0)

    if predictions is None:
        blk = R.bfcl_blocker()
        console.print(f"\n[yellow]Per-model reproduction:[/] {blk['blocker']}"
                      if not R.model_predictions_available()
                      else "[dim]Key present — use --live to run, or --predictions "
                           "to score a file.[/]")
        raise typer.Exit(0)

    preds = json.loads(Path(predictions).read_text())
    res = R.reproduce_from_predictions(
        split, model, preds, published_accuracy=published,
        published_source=published_source or None, full=full)
    d = res.to_dict()
    console.print(f"\nModel [bold]{model}[/] on BFCL {split}: reproduced accuracy "
                  f"[bold]{d['reproduced_accuracy']:.1%}[/] (n={d['n']}, "
                  f"Wilson95 [{d['wilson_low']:.3f},{d['wilson_high']:.3f}])")
    if published is not None:
        verdict = "[green]REPRODUCED[/]" if d["reproduced"] else "[red]MISMATCH[/]"
        console.print(f"Published {published:.1%} → {verdict} "
                      f"(published within our 95% interval: {d['reproduced']})")


@app.command()
def regress(agent: str = typer.Option(..., "--agent", "-a", help="agent id"),
            config: str = "config.yaml"):
    """Re-run every suite this agent was scored on; diff against prior results."""
    cfg, reg = _ctx(config)
    for suite_id in reg.suites_scored_for(agent):
        history = reg.scorecards_for(agent, suite_id)
        previous = history[-1]
        adapter = ops.build_adapter(cfg, variant="reference", agent_id=agent)
        sc = asyncio.run(ops.run_and_score_op(cfg, reg, adapter, suite_id))
        delta = sc.task_success_rate - previous.task_success_rate
        colour = "green" if delta >= 0 else "red"
        console.print(f"{suite_id}: {previous.task_success_rate:.0%} → "
                      f"[{colour}]{sc.task_success_rate:.0%}[/]")


@app.command()
def report(scorecard_id: str, out: Path = Path("report.md"),
           config: str = "config.yaml"):
    """Render a scorecard to a client-ready Markdown report."""
    cfg, reg = _ctx(config)
    out.write_text(ops.report_op(reg, scorecard_id))
    console.print(f"Wrote {out}")


@app.command(name="inspect-export")
def inspect_export(scorecard_id: str,
                   out: Path = typer.Option(None, "--out", "-o",
                       help="write to this file (default: <scorecard>.inspect.json)"),
                   config: str = "config.yaml"):
    """Export a scorecard as a UK AISI Inspect (inspect_ai) EvalLog (JSON).

    The output validates against inspect_ai.log.EvalLog and opens in the Inspect
    viewer / re-scores in the Inspect harness — no inspect_ai install required."""
    import json
    _, reg = _ctx(config)
    log = ops.inspect_log_op(reg, scorecard_id)
    out = out or Path(f"{scorecard_id}.inspect.json")
    out.write_text(json.dumps(log, indent=2))
    n = len(log.get("samples", []))
    console.print(f"Wrote {out}  ({n} sample(s), Inspect EvalLog v{log['version']})")


@app.command(name="inspect-import")
def inspect_import(path: Path = typer.Argument(..., help="an Inspect EvalLog .json"),
                   save: bool = typer.Option(False, "--save",
                       help="persist the reconstructed scorecard/traces to the registry"),
                   config: str = "config.yaml"):
    """Parse an Inspect EvalLog back into an agenttic scorecard (+ traces/rubric).

    Lossless for logs agenttic produced; best-effort for foreign logs (scores
    snap to {0,0.5,1}, aggregates recomputed). With --save, the recovered
    records are written to the registry."""
    import json
    from agenttic.interop import from_inspect_log
    _, reg = _ctx(config)
    result = from_inspect_log(json.loads(path.read_text()))
    sc = result["scorecard"]
    console.print(f"Recovered scorecard {sc.scorecard_id}: agent={sc.agent_id} "
                  f"suite={sc.suite_id} v{sc.suite_version} "
                  f"success={sc.task_success_rate:.2%} "
                  f"runs={len(sc.run_scores)} traces={len(result['traces'])}")
    if save:
        for tr in result["traces"]:
            reg.save_trace(tr)
        if result["rubric"]:
            reg.save_rubric(result["rubric"])
        reg.save_scorecard(sc)
        console.print(f"Saved to registry (tenant={_STATE['tenant'] or 'default'}).")


def _ab_variant(reg, label: str, agent: str, model: str, prompt: str):
    """Build an ABVariant from a base agent id, resolving a declared catalog
    entry when present; --model/--prompt override it (the model/prompt A/B
    cases)."""
    from agenttic.registry.sqlite_store import NotFoundError
    from agenttic.schema.ab import ABVariant
    fields = {"label": label, "agent_id": agent, "model": model,
              "system_prompt": prompt}
    try:
        d = reg.get_declared_agent(agent)
        fields.update(variant=d.variant, url=d.url,
                      managed_agent_id=d.managed_agent_id,
                      environment_id=d.environment_id,
                      model=model or d.model,
                      system_prompt=prompt or d.system_prompt,
                      cost_per_call_usd=d.cost_per_call_usd,
                      expected_input_tokens=d.expected_input_tokens,
                      expected_output_tokens=d.expected_output_tokens)
    except NotFoundError:
        pass
    return ABVariant(**fields)


@app.command()
def ab(suite: str = typer.Option(..., "--suite", "-s", help="suite id to run"),
       a: str = typer.Option(..., "--a", help="variant A agent id"),
       b: str = typer.Option(..., "--b", help="variant B agent id"),
       a_model: str = "", b_model: str = "",
       a_prompt: str = "", b_prompt: str = "",
       a_label: str = "A", b_label: str = "B",
       out: Path = typer.Option(None, "--out", help="write the Markdown report"),
       config: str = "config.yaml"):
    """Run two variants head-to-head on one suite and print the verdict.

    Each --a/--b is an agent id (resolved from the declared catalog if present);
    --a-model/--b-model and --a-prompt/--b-prompt override the model or system
    prompt, so the same agent can be compared across models or prompts. Both runs
    use the same suite, rubric and judge — a paired comparison with a McNemar
    significance test."""
    from agenttic.ab import run_ab_op
    cfg, reg = _ctx(config)
    va = _ab_variant(reg, a_label, a, a_model, a_prompt)
    vb = _ab_variant(reg, b_label, b, b_model, b_prompt)
    comp = asyncio.run(run_ab_op(cfg, reg, suite, va, vb))
    color = "green" if comp.winner != "tie" else "yellow"
    console.print(f"[bold]A/B {comp.comparison_id}[/] — [{color}]{comp.verdict}[/]")
    console.print(f"  {comp.label_a} {comp.success_rate_a:.0%} vs "
                  f"{comp.label_b} {comp.success_rate_b:.0%} "
                  f"on {comp.n_paired} paired case(s)")
    if out:
        out.write_text(ops.ab_report_op(reg, comp.comparison_id))
        console.print(f"Wrote {out}")


@app.command()
def optimize(
    suite: str = typer.Option(..., "--suite", "-s", help="suite id to optimize against"),
    agent: str = typer.Option("agent-under-test", "--agent", "-a",
                              help="agent id under optimization"),
    prompt: str = typer.Option("", "--prompt", "-p",
                               help="baseline system prompt (blank = none)"),
    prompt_file: Path = typer.Option(None, "--prompt-file",
                                     help="read the baseline prompt from a file"),
    rounds: int = typer.Option(2, "--rounds", help="optimization rounds"),
    candidates: int = typer.Option(3, "--candidates",
                                   help="candidate prompts proposed per round"),
    heldout: float = typer.Option(0.3, "--heldout",
                                  help="fraction of the suite held out (overfitting guard)"),
    model: str = typer.Option("", "--model", help="agent model override (frozen across the run)"),
    max_runs: int = typer.Option(60, "--max-runs", help="hard cap on suite executions"),
    out: Path = typer.Option(None, "--out", help="write the best prompt to a file"),
    config: str = "config.yaml"):
    """Self-improving system-prompt loop: hold the model frozen, treat the suite
    score as the reward, and iteratively edit the SYSTEM PROMPT to fix failing
    criteria (OPRO/ProTeGi reflective optimization).

    A held-out slice is split off that the optimizer never sees, so train vs
    held-out scores expose overfitting. A candidate is adopted only on a paired
    pass-rate improvement with NO significantly-regressed criterion. The loop
    runs the suite many times — your own key pays for each (bounded by
    --rounds/--candidates/--max-runs)."""
    from agenttic import optimizer as optmod
    cfg, reg = _ctx(config)
    baseline = prompt_file.read_text() if prompt_file else prompt

    def _on(event: str, data: dict) -> None:
        if event == "cost_projection":
            console.print(f"[yellow]~{data['projected_agent_runs']} suite "
                          f"executions projected[/] (train={data['n_train']}, "
                          f"heldout={data['n_heldout']}, cap={data['max_agent_runs']})")
        elif event == "propose":
            console.print(f"  round {data['round']}: targeting "
                          f"{', '.join(data['failing_criteria']) or '—'}")
        elif event == "candidate":
            tag = "[green]✓ accept[/]" if data["accepted"] else "[dim]✗ reject[/]"
            console.print(f"    cand {data['index']}: {tag} — {data['reason']}")

    run = asyncio.run(optmod.optimize(
        cfg, reg, agent, suite, rounds=rounds, candidates_per_round=candidates,
        heldout_fraction=heldout, baseline_prompt=baseline, model=model,
        max_agent_runs=max_runs, on_progress=_on))

    verb = "improved" if run.improved else "no improvement found"
    console.print(f"\n[bold]Optimization {run.run_id}[/] — {verb}")
    console.print(f"  train:   {run.baseline_train_rate:.0%} → "
                  f"{run.best_train_rate:.0%} ([bold]{run.train_gain:+.0%}[/])")
    if run.best_heldout_rate is not None:
        gap = run.overfit_gap
        flag = " [red](overfit risk)[/]" if gap is not None and gap > 0.15 else ""
        console.print(f"  heldout: {run.baseline_heldout_rate:.0%} → "
                      f"{run.best_heldout_rate:.0%} "
                      f"([bold]{run.heldout_gain:+.0%}[/]){flag}")
        if gap is not None:
            console.print(f"  overfit gap (train gain − heldout gain): {gap:+.0%}")
    else:
        console.print("  heldout: [dim]none (suite too small to hold out)[/]")
    console.print(f"  {run.n_agent_runs} suite executions, "
                  f"cost ${run.total_cost_usd:.4f}, {run.best_version} accepted edit(s)")
    if out and run.best_prompt:
        out.write_text(run.best_prompt)
        console.print(f"Wrote best prompt to {out}")


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


@app.command()
def pilot(config: str = "config.yaml",
          approve_suite: bool = typer.Option(False, "--approve")):
    """Seed the registry with the example pilot suite (support-ticket triage,
    10 cases + rubric) so the UI's starter workflow runs out of the box."""
    import json

    from agenttic.registry.sqlite_store import DuplicateVersionError
    from agenttic.schema.rubric import Rubric
    from agenttic.schema.testcase import TestCase, TestSuite

    pilot_dir = Path(__file__).resolve().parents[2] / "examples" / "pilot_support_triage"
    _, reg = _ctx(config)
    rubric = Rubric.model_validate_json((pilot_dir / "rubric.json").read_text())
    suite = TestSuite.model_validate_json((pilot_dir / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((pilot_dir / "cases.json").read_text())]
    try:
        reg.save_rubric(rubric)
        reg.save_suite(suite, cases)
        console.print(f"Seeded suite [bold]{suite.suite_id}[/] v{suite.version} "
                      f"({len(cases)} cases) + rubric {rubric.rubric_id}.")
    except DuplicateVersionError:
        console.print(f"Suite {suite.suite_id} v{suite.version} already seeded.")
    if approve_suite:
        reg.approve_suite(suite.suite_id, suite.version)
        console.print("[green]Approved[/] — runnable immediately.")
    else:
        console.print("Still DRAFT: approve in the UI (Resources → suites) or "
                      f"`uv run agenttic approve {suite.suite_id}`.")


@app.command()
def retention(apply: bool = typer.Option(False, "--apply",
                                         help="perform redaction/pruning (default is dry-run)"),
              config: str = "config.yaml"):
    """Apply the trace retention policy (config `retention`): redact old trace
    inputs/outputs (PII) and prune very old traces. Run on a schedule. Operates
    on the default tenant's DB; for Postgres it covers all tenants in that DB."""
    cfg, reg = _ctx(config)
    r = cfg.get("retention", {}) or {}
    redact_days = int(r.get("trace_redact_days", 0) or 0)
    prune_days = int(r.get("trace_prune_days", 0) or 0)
    if apply:
        redacted = reg.redact_old_traces(redact_days)
        pruned = reg.prune_traces(prune_days)
        console.print(f"[green]Retention applied[/]: redacted {redacted}, "
                      f"pruned {pruned} traces.")
    else:
        console.print(f"[yellow]Dry run[/] (use --apply): would redact traces "
                      f">{redact_days}d and prune traces >{prune_days}d "
                      f"(0 = disabled).")


@app.command()
def migrate(status: bool = typer.Option(False, "--status",
                                        help="show applied/pending and exit"),
            config: str = "config.yaml"):
    """Apply schema migrations to the registry DB (idempotent). Building the
    Registry already migrates to head; this reports or re-runs explicitly."""
    from agenttic.migrations import migration_status, run_migrations

    _, reg = _ctx(config)  # constructing the Registry runs migrations
    if status:
        st = migration_status(reg.engine)
        console.print(f"applied={st['applied']} pending={st['pending']} "
                      f"head={st['head']}")
        return
    applied = run_migrations(reg.engine)
    console.print(f"Applied migrations: {applied}" if applied
                  else "[green]Schema up to date.[/]")


def _resolve_ui_binding(cfg: dict, host: str, port: int, lan: bool) -> tuple[str, int]:
    """Precedence: --lan > --host/--port flags > config.yaml ui section >
    loopback defaults."""
    ui_cfg = cfg.get("ui", {}) or {}
    resolved_host = "0.0.0.0" if lan else (host or str(ui_cfg.get("host", "127.0.0.1")))
    resolved_port = port or int(ui_cfg.get("port", 8700))
    return resolved_host, resolved_port


def _lan_ip() -> str | None:
    """Best-effort primary LAN address (no packets actually sent)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


@app.command()
def ui(host: str = "", port: int = 0,
       lan: bool = typer.Option(False, "--lan",
                                help="Bind 0.0.0.0 so other devices on your "
                                     "network can reach the UI."),
       config: str = "config.yaml"):
    """Launch the visual workflow builder (FastAPI + the React canvas)."""
    import uvicorn
    from agenttic.server.app import UI_DIST, create_app

    cfg = load_config(config)
    host, port = _resolve_ui_binding(cfg, host, port, lan)

    if not UI_DIST.is_dir():
        console.print(
            "[yellow]ui/dist not found — running API-only.[/] Build the "
            "frontend with `npm --prefix ui install && npm --prefix ui run "
            "build`, or develop with `npm --prefix ui run dev` (proxies /api "
            f"to http://{host}:{port}).")
    console.print(f"Agenttic UI on [bold]http://{host}:{port}[/]")
    if host != "127.0.0.1":
        from agenttic.server.auth import configured_token

        ip = _lan_ip()
        if ip:
            console.print(f"LAN: [bold]http://{ip}:{port}[/]")
        if configured_token(cfg):
            console.print("[green]Auth:[/] API token required for all /api routes.")
        else:
            console.print(
                "[yellow]Warning:[/] no API token set — anyone on this network "
                "can edit workflows, approve suites, and trigger runs that spend "
                "your Anthropic credits. Set AGENTTIC_API_TOKEN (or auth.token) "
                "before exposing to a network.")
    uvicorn.run(create_app(config), host=host, port=port, log_level="info")


agents_app = typer.Typer(help="Declared agent catalog: pre-register agents so "
                              "they're pickable for runs and typed on the Index.")
app.add_typer(agents_app, name="agents")


@agents_app.command("add")
def agents_add(
    agent_id: str,
    variant: str = typer.Option("reference", "--variant", "-v",
                                help="reference | blackbox | managed"),
    model: str = typer.Option("", help="reference: model override"),
    system_prompt: str = typer.Option("", help="reference: task instructions"),
    url: str = typer.Option("", help="blackbox: HTTP endpoint"),
    managed_agent_id: str = typer.Option("", help="managed: agent id"),
    environment_id: str = typer.Option("", help="managed: environment id"),
    description: str = typer.Option("", help="free-text note"),
    config: str = "config.yaml",
):
    """Register an agent (or store the next version of an existing one)."""
    from pydantic import ValidationError

    from agenttic.schema.agent import DeclaredAgent
    from agenttic.security import UnsafeURLError, validate_blackbox_url

    cfg, reg = _ctx(config)
    try:
        agent = DeclaredAgent(
            agent_id=agent_id, variant=variant, model=model,
            system_prompt=system_prompt, url=url,
            managed_agent_id=managed_agent_id, environment_id=environment_id,
            description=description)
    except ValidationError as exc:
        raise typer.BadParameter(str(exc))
    if agent.variant == "blackbox":
        try:
            validate_blackbox_url(agent.url, cfg=cfg, allow_unresolved=True)
        except UnsafeURLError as exc:
            raise typer.BadParameter(f"unsafe agent url: {exc}")
    saved = reg.register_agent(agent)
    console.print(f"[green]Registered[/] {saved.agent_id} v{saved.version} "
                  f"({saved.variant}).")


@agents_app.command("correlate")
def agents_correlate(
        agent: str = typer.Option(..., "--agent", "-a", help="agent id"),
        limit: int = typer.Option(200, help="how many ingested traces to search"),
        config: str = "config.yaml"):
    """Join stored runs to the spans the agent exported about itself.

    The zero-adapter-code path to glass-box evidence: the harness stamps
    ``gen_ai.conversation.id`` on each run, the agent stamps the same id on the
    OpenTelemetry spans it exports, and this joins them. An agent that exports
    nothing is reported as exactly that — nothing is upgraded on faith.
    """
    from agenttic.ingest.correlate import correlate_all

    _, reg = _ctx(config)
    driven = [t for t in reg.traces(agent, mode="batch")][-limit:]
    exported = [t for t in reg.traces(agent, mode="live")][-limit:]
    if not driven:
        console.print(f"[yellow]No stored runs for {agent}.[/]")
        raise typer.Exit(code=1)

    _, summary = correlate_all(driven, exported)
    table = Table("driven runs", "exported traces", "correlated",
                  "spans attached", "upgraded to glass box")
    table.add_row(str(summary["of_traces"]), str(len(exported)),
                  str(summary["correlated"]), str(summary["attached_spans"]),
                  str(summary["upgraded_to_glass_box"]))
    console.print(table)
    console.print(f"[dim]{summary['note']}[/]")
    if not summary["correlated"]:
        console.print(
            "\n[yellow]Nothing correlated.[/] For this to work the agent must "
            "export OpenTelemetry spans carrying `gen_ai.conversation.id`, and "
            "they must be ingested (`agenttic ingest otel <spans.json>`).")


@agents_app.command("drivers")
def agents_drivers(
        check: str = typer.Option("", "--check", help="probe one agent"),
        model: str = typer.Option("", "--model", help="model to pin for the probe"),
        config: str = "config.yaml"):
    """List the agents declared in config — the no-code way to add a subject.

    An agent belongs in ``config.yaml`` under ``agents:``, not in a Python
    module. ``--check`` starts it and reports what it actually supports, which
    is the difference between a declaration and a working integration.
    """
    import shutil as _shutil

    cfg, _ = _ctx(config)
    declared = ops.declared_agents(cfg)
    if not declared:
        console.print(
            "[yellow]No agents declared.[/] Add one under `agents:` in "
            "config.yaml:\n"
            "  agents:\n"
            "    my-agent:\n"
            "      driver: acp                 # acp (preferred) | cli\n"
            "      command: [\"my-agent\", \"acp\"]\n")
        raise typer.Exit(code=1)

    table = Table("agent", "driver", "command", "version", "on PATH")
    for name, spec in sorted(declared.items()):
        cmd = list(spec.get("command") or [])
        found = bool(cmd) and (_shutil.which(cmd[0]) is not None or "/" in cmd[0])
        table.add_row(name, str(spec.get("driver") or "cli"), " ".join(cmd[:4]),
                      str(spec.get("version") or "—"),
                      "[green]yes[/]" if found else "[red]no[/]")
    console.print(table)
    console.print(
        "[dim]acp declares tool kinds, status and usage, so action_risk and cost "
        "are measured rather than guessed. Prefer it when the agent speaks it.[/]")

    if not check:
        return
    adapter = ops.build_adapter(cfg, variant=str(
        declared.get(check, {}).get("driver") or "cli"), agent_id=check,
        model=model)
    if not model:
        console.print(
            "[yellow]No --model given[/], so the agent uses whatever it has "
            "stored. The probe cannot then say which model answered.")
    console.print(f"\n[bold]Probing {check}[/] — one trivial task")
    trace = adapter.run({"task": "Reply with the single word: ready. Do nothing else."},
                        test_case_id="probe")
    ok = not trace.final_output.startswith("HARNESS_FAILURE")
    console.print(f"  answered: {'[green]yes[/]' if ok else '[red]no[/]'}")
    console.print(f"  visibility: {trace.visibility}   spans: {len(trace.spans)}")
    console.print(f"  sessions (multi-turn): {type(adapter).supports_sessions()}")
    kinds = sorted({s.kind for s in trace.spans})
    console.print(f"  span kinds: {', '.join(kinds)}")
    for s in trace.spans:
        for d in (s.attributes or {}).get("disclosures") or []:
            console.print(f"  [yellow]disclosed:[/] {d}")
    if not ok:
        console.print(f"  [red]{trace.final_output[:200]}[/]")
        raise typer.Exit(code=1)


@agents_app.command("list")
def agents_list(all_: bool = typer.Option(False, "--all",
                                          help="include retired agents"),
                config: str = "config.yaml"):
    """List declared agents."""
    _, reg = _ctx(config)
    rows = reg.list_declared_agents(include_retired=all_)
    if not rows:
        console.print("No declared agents. Add one with `uv run agenttic agents add`.")
        return
    table = Table("agent", "type", "version", "connection", "active")
    for a in rows:
        conn = (a["url"] or a["managed_agent_id"]
                or a["model"] or "config default")
        table.add_row(a["agent_id"], a["variant"], str(a["version"]), conn,
                      "✓" if a["active"] else "[red]retired[/]")
    console.print(table)


@agents_app.command("show")
def agents_show(agent_id: str, config: str = "config.yaml"):
    """Show one declared agent's full details."""
    from agenttic.registry.sqlite_store import NotFoundError

    _, reg = _ctx(config)
    try:
        agent = reg.get_declared_agent(agent_id)
    except NotFoundError:
        raise typer.BadParameter(f"no declared agent {agent_id!r}")
    for k, v in agent.model_dump().items():
        console.print(f"  [bold]{k}[/]: {v}")


@agents_app.command("retire")
def agents_retire(agent_id: str, config: str = "config.yaml"):
    """Retire an agent (soft-delete; history kept, re-add to revive)."""
    from agenttic.registry.sqlite_store import NotFoundError

    _, reg = _ctx(config)
    try:
        reg.retire_agent(agent_id)
    except NotFoundError:
        raise typer.BadParameter(f"no declared agent {agent_id!r}")
    console.print(f"[yellow]Retired[/] {agent_id}.")


users_app = typer.Typer(help="Manage login accounts (Postgres/SQLite users).")
app.add_typer(users_app, name="users")


@users_app.command("create")
def users_create(
    email: str,
    password: str = typer.Option(..., "--password", "-p",
                                 prompt=True, hide_input=True,
                                 help="min 8 chars (prompted, hidden)"),
    role: str = typer.Option("admin", "--role", help="viewer | operator | admin"),
    tenant: str = typer.Option("default", "--tenant-id",
                               help="workspace this user belongs to"),
    config: str = "config.yaml",
):
    """Create a login account (use this to bootstrap the first admin)."""
    from agenttic.server.users import DuplicateUserError, UserStore

    _, reg = _ctx(config)
    try:
        u = UserStore(reg.engine).create_user(email, password, role=role,
                                              tenant=tenant)
    except DuplicateUserError:
        raise typer.BadParameter(f"user {email} already exists")
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    console.print(f"[green]Created[/] {u.email} (role={u.role}, "
                  f"tenant={u.tenant_id}).")


@users_app.command("set-password")
def users_set_password(
    email: str,
    password: str = typer.Option(..., "--password", "-p",
                                 prompt=True, hide_input=True,
                                 help="new password, min 8 chars (prompted, hidden)"),
    config: str = "config.yaml",
):
    """Reset an existing account's password."""
    from agenttic.server.users import UserStore

    _, reg = _ctx(config)
    try:
        ok = UserStore(reg.engine).set_password(email, password)
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    if not ok:
        raise typer.BadParameter(f"user {email} not found")
    console.print(f"[green]Password reset[/] for {email}.")


@users_app.command("list")
def users_list(config: str = "config.yaml"):
    """List login accounts (emails + roles; no password material)."""
    from sqlmodel import Session, select

    from agenttic.registry.sqlite_store import UserRow
    _, reg = _ctx(config)
    with Session(reg.engine) as s:
        rows = s.exec(select(UserRow).order_by(UserRow.email)).all()
    if not rows:
        console.print("No users. Create one with `uv run agenttic users create`.")
        return
    table = Table("email", "role", "tenant", "created")
    for u in rows:
        table.add_row(u.email, u.role, u.tenant_id, u.created_at.strftime("%Y-%m-%d"))
    console.print(table)


surface_app = typer.Typer(
    help="What an agent DECLARES it can do — and the test space derived from it.")
app.add_typer(surface_app, name="surface")


@surface_app.command("list")
def surface_list():
    """Every agent surface this build can describe."""
    from agenttic.redteam.descriptor import _SURFACES

    table = Table("surface", "agent id", "tools", "workflows")
    for name in sorted(_SURFACES):
        d = _SURFACES[name]()
        table.add_row(name, d.agent_id, str(len(d.tools)),
                      ", ".join(w.workflow_id for w in d.workflows) or "—")
    console.print(table)


@surface_app.command("show")
def surface_show(name: str = typer.Argument(..., help="surface name"),
                 config: str = "config.yaml"):
    """Show the surface, the space derived from it, and what cannot be tested.

    The last part is the point. A tool whose risk class the agent never declared
    cannot be held to a forbidden-tool expectation, and a dimension nothing can
    realize is a knob wired to nothing — both are named here rather than left for
    a reader to assume the space covers them.
    """
    from agenttic.redteam.descriptor import resolve_surface
    from agenttic.stimulus import (derive_space, reachable_values,
                                   realization_findings)

    try:
        d = resolve_surface(name)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(f"[bold]{d.agent_id}[/bold]  ({len(d.tools)} tools, "
                  f"{len(d.workflows)} workflows)")

    tools = Table("tool", "mutating", "irreversible")
    for t in d.tools:
        def _cell(v):
            return "—  [dim]undeclared[/dim]" if v is None else ("yes" if v else "no")
        tools.add_row(t.name, _cell(getattr(t, "mutating", None)),
                      _cell(getattr(t, "irreversible", None)))
    console.print(tools)

    space = derive_space(d)
    console.print(f"\nderived space: [bold]{space.ref()}[/bold]  "
                  f"fingerprint {space.fingerprint()[:16]}")
    reach = reachable_values(space)
    dims = Table("dimension", "reachable values")
    for dim in space.dimensions:
        dims.add_row(dim.dim_id, ", ".join(sorted(reach.get(dim.dim_id, []))) or "—")
    console.print(dims)

    findings = realization_findings(space)
    if findings:
        console.print("\n[yellow]declared but not realizable[/yellow] — a "
                      "dimension nothing varies is a knob wired to nothing:")
        for f in findings:
            console.print(f"  · {f}")

    undeclared = [t.name for t in d.tools if getattr(t, "mutating", None) is None]
    if undeclared:
        console.print("\n[yellow]risk class undeclared[/yellow] — these cannot be "
                      "held to a forbidden-tool expectation:")
        console.print("  " + ", ".join(undeclared))


@surface_app.command("save")
def surface_save(name: str = typer.Argument(..., help="surface name"),
                 config: str = "config.yaml"):
    """Derive the space and store it, so `agenttic cdv --space <id>` can run it."""
    from agenttic.redteam.descriptor import resolve_surface
    from agenttic.registry.sqlite_store import DuplicateVersionError
    from agenttic.stimulus import derive_space

    _, reg = _ctx(config)
    try:
        space = derive_space(resolve_surface(name))
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    try:
        reg.save_scenario_space(space)
        console.print(f"saved [bold]{space.ref()}[/bold]")
    except DuplicateVersionError:
        console.print(f"[dim]{space.ref()} already stored[/dim]")
    console.print(f"run it: agenttic cdv --agent <id> --rubric <id> "
                  f"--space {space.space_id}")


# --------------------------------------------------------------------------- #
# Scenarios (P7): run ONE against the world, and read a stored run back.
#
# `agenttic cdv` runs hundreds of scenarios and reports closure over the batch;
# nothing could run ONE and show what happened to it. These three commands are
# that path, and the first consumer of `Registry.save_scenario_run` — without
# which a run's transcript, fault report, state diff and refused calls were
# assembled by the runner and dropped on the floor when the process exited.
# --------------------------------------------------------------------------- #
_SCENARIO_SPACE_ID = "space-conversational_transactional"

#: What ended a conversation, in words. EXACT keys, never a prefix or substring
#: test: `turn_cap` is OUR ceiling and `gave_up` is the counterparty leaving, and
#: a line that printed both as "ended" would hide the only interesting half of
#: the fact — which side stopped talking. A reason this table does not know
#: prints bare rather than glossed; inventing a meaning for an unknown token is
#: how a reader comes to trust one.
_ENDED_MEANS = {
    "satisfied": "the customer got what they came for",
    "gave_up": "the customer ran out of patience and left",
    "turn_cap": "WE stopped asking (--max-turns) — the customer did not leave",
    "unresolved": "it stopped with the customer's problem still open",
    "record_exhausted": "a replayed counterparty ran out of recorded turns",
    "simulator_error": "the counterparty simulator failed — this run measured "
                       "the simulator, not the agent",
}

#: Enforcement verdicts a world tool call can carry (``scenario/env.py``). THREE
#: values, not two: `faulted` is a call the gateway ALLOWED and the injector then
#: broke, which is the environment's doing and not the harness's, and a
#: two-valued renderer would have to file it under one of the other two.
_ENFORCEMENT = {
    "executed": "[green]executed[/]",
    "blocked": "[red]BLOCKED[/]",
    "faulted": "[yellow]faulted[/]",
}


def _scenario_space(reg, space_id: str):
    """The stored space, or the seeded one — the same fallback `cdv` makes.

    Kept identical on purpose: an operator who runs one scenario and then a CDV
    loop over "the same space" must get the same space, and two fallbacks are
    where that stops being true.
    """
    from agenttic.registry.sqlite_store import NotFoundError
    from agenttic.stimulus.spaces.conversational_transactional import seed_space
    try:
        return reg.get_scenario_space(space_id)
    except NotFoundError:
        if space_id != _SCENARIO_SPACE_ID:
            raise typer.BadParameter(
                f"no scenario space {space_id!r} in the registry; the only "
                f"seeded space is {_SCENARIO_SPACE_ID}. `agenttic surface save "
                "<surface>` stores a space derived from an agent's own "
                "declared surface.") from None
        return seed_space()


def _short(value, limit: int = 44) -> str:
    """One cell's worth of a stored value, TRUNCATION MARKED.

    A cut string that does not say it was cut is a value a reader will quote.
    """
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


#: A timestamp string that already carries its zone: a trailing ``Z`` or a
#: ``+HH:MM`` / ``-HHMM`` offset.
_HAS_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


def _utc(value) -> str:
    """A stored timestamp with its ZONE SAID.

    ``_now()`` writes ``datetime.now(timezone.utc)`` and SQLite hands it back
    NAIVE, so the string has no offset on it and a reader in Lisbon has no way
    to tell it is not their own clock. The console page already labels the same
    field UTC; this is the CLI matching it.

    A label, never a conversion — and never a second one: a backend that does
    return the offset (Postgres) has already said UTC in its own notation and is
    left exactly as it came.
    """
    text = str(value or "")
    if not text or _HAS_OFFSET.search(text):
        return text
    return f"{text} UTC"


def _fault_line(f: dict) -> str:
    """One fault, said the way ``PlannedFault.describe`` says it.

    Every field is escaped, including the numeric ones: these come out of a
    stored payload, and rich eats anything in square brackets as a style tag —
    the defect that printed a tool called ``fetch[v2]`` as ``fetch``.
    """
    line = (f"{escape(str(f.get('kind', '?')))} on call "
            f"#{escape(str(f.get('call_index', '?')))}"
            f" of {escape(str(f.get('tool', '?')))}")
    if f.get("step") is not None:
        line += f" [dim](step {escape(str(f['step']))})[/]"
    return line


def _render_run_header(run: dict) -> None:
    """Identity, the point the solver drew, and the ticket it realized.

    The two hashes on the space line are not the same KIND of value and must not
    print as though they were. ``space_fingerprint`` IS 16 characters
    (``StimulusSpace.fingerprint`` is ``sha256()[:16]``) — a whole value someone
    can match against a space. ``content_sha256`` is 64, and it used to be cut to
    16 with no marker, sitting immediately beside the whole one: two identical
    looking tokens, one of which is a quarter of itself, with nothing on the line
    saying which. It goes through :func:`_short`, so the cut one carries the cut.
    """
    console.print(f"[bold]scenario run[/] {escape(run['run_id'])}")
    console.print(f"  scenario [bold]{escape(run['scenario_id'])}[/] · agent "
                  f"[cyan]{escape(run['agent_id'])}[/] · seed {run['seed']}")
    content = str((run.get("derived") or {}).get("content_sha256") or "")
    console.print(f"  space {escape(run['space_ref'])} · fingerprint "
                  f"{escape(run['space_fingerprint'][:16])} · content "
                  + (escape(_short(content, 17)) if content
                     else "[dim]not recorded[/]"))
    if run.get("created_at"):
        console.print(f"  [dim]run at {escape(_utc(run['created_at']))}[/]")
    point = run.get("point") or {}
    console.print("  point: " + (", ".join(
        f"{escape(k)}={escape(str(v))}" for k, v in sorted(point.items()))
        or "[dim]not recorded[/]"))
    console.print(f"\n[bold]ticket[/]\n  {escape(run.get('ticket') or '')}")


def _render_tool_calls(trace) -> None:
    """Every world tool call with the gateway's verdict on it.

    Read off the TRACE, not off the outcome: the outcome carries only the NAMES
    of the calls that were refused, and "the harness allowed this one and the
    injector then broke it" is a third state a blocked-list cannot express.
    """
    from agenttic.scenario.faults import FAULT_ATTR
    console.print("\n[bold]tool calls[/] — each one evaluated by the "
                  "enforcement gateway before it ran")
    if trace is None:
        console.print("  [yellow]the trace is no longer stored, so the calls "
                      "cannot be listed[/] — this is a missing record, not an "
                      "agent that called nothing.")
        return
    spans = [s for s in trace.spans if s.kind == "tool_call"]
    if not spans:
        console.print("  the agent called no tool.")
        return
    table = Table("#", "tool", "enforcement", "gateway", "fault", "error")
    for i, s in enumerate(spans, 1):
        attrs = s.attributes or {}
        verdict = attrs.get("enforcement")
        if verdict is None:
            cell = "[dim]not recorded[/]"
        else:
            cell = _ENFORCEMENT.get(verdict, escape(str(verdict)))
        table.add_row(str(i), escape(s.name), cell,
                      escape(str(attrs.get("decision_action") or "—")),
                      escape(str(attrs.get(FAULT_ATTR) or "—")),
                      escape(_short(s.error or "—")))
    console.print(table)


def _render_faults(faults: dict) -> None:
    """The four fault lists as FOUR DIFFERENT FACTS.

    `fired` is the only one that happened. `skipped` reached its call and could
    not fire, and its reason is the finding. `never_reached` was staged on a call
    the agent never made — a finding about the AGENT, and the one sentence a
    fault report exists to be able to say. Dropping it would let "we staged a
    timeout and the agent never called that tool" read as "the world behaved".
    And a run with no report at all is none of the three.
    """
    console.print("\n[bold]faults[/] — planned is not fired, and never-reached "
                  "is not skipped")
    if not faults.get("recorded"):
        console.print("  [yellow]no fault report was recorded for this run[/] — "
                      "which is not the same claim as no fault having been "
                      "staged.")
        return
    if faults.get("problem"):
        console.print("  [yellow]the report could not be reconstructed[/]: "
                      f"{escape(str(faults['problem']))} — the stored lists are "
                      "shown as they are, and never-reached is not derived.")
    source = str(faults.get("source") or "none")
    planned = faults.get("planned") or []
    # "Nothing was staged" is a claim about the WHOLE report, so it has to be
    # read off the whole report. Asserting it from `planned` alone let a stored
    # row carrying a FIRED fault print "the world was left to behave" and then
    # return without showing the fault — an absence asserted over evidence that
    # is right there, which is the one thing this renderer exists to prevent.
    outcomes = ((faults.get("fired") or []) + (faults.get("skipped") or [])
                + (faults.get("never_reached") or []))
    if not planned and not outcomes:
        console.print(f"  nothing was staged [dim](source: {escape(source)})[/] "
                      "— the world was left to behave.")
        return
    if not planned:
        # The plan is empty and the outcomes are not. One of the two is wrong and
        # this surface cannot tell which, so it says so and shows the outcomes
        # rather than picking the reading that happens to be reassuring.
        console.print(f"  [yellow]the stored plan is empty and "
                      f"{len(outcomes)} outcome(s) are recorded against it[/] "
                      f"[dim](source: {escape(source)})[/] — the report "
                      "disagrees with itself; the outcomes are shown as stored.")
    if planned:
        console.print(f"  {len(planned)} staged "
                      f"[dim](source: {escape(source)})[/]")
    for f in faults.get("fired") or []:
        note = ("  [dim]— fired but UNOBSERVABLE: the agent saw no difference[/]"
                if f.get("observable") is False else "")
        console.print(f"  [red]FIRED[/]          {_fault_line(f)}{note}")
    for f in faults.get("skipped") or []:
        console.print(f"  [yellow]SKIPPED[/]        {_fault_line(f)} — "
                      f"{escape(str(f.get('reason') or 'no reason recorded'))}")
    never = faults.get("never_reached")
    if never is None:
        console.print("  [yellow]never-reached could not be derived[/] from the "
                      "stored plan, so it is not reported as empty.")
    else:
        for f in never:
            console.print(f"  [yellow]NEVER REACHED[/]  {_fault_line(f)} — it was "
                          "staged and the agent never made that call")
    if never is not None and not (faults.get("fired") or faults.get("skipped")
                                  or never):
        # Unreachable from a report `FaultPlan.report` produced: never_reached is
        # planned minus fired-and-skipped, so an empty plan is the only way all
        # three empty out. Printing the contradiction beats printing nothing
        # under a heading that says a fault was staged.
        console.print(f"  [yellow]the report accounts for none of the "
                      f"{len(planned)} staged fault(s)[/] — it does not say they "
                      "fired, were skipped, or were never reached.")


def _render_state_diff(diff: dict) -> None:
    """What the run did to the world. ``{}`` is a sentence, not an empty box."""
    console.print("\n[bold]state diff[/] — what the run did to the world")
    if not diff:
        console.print("  the world was not changed: no field moved.")
        return
    table = Table("field", "before", "after")
    for path in sorted(diff):
        change = diff[path] if isinstance(diff[path], dict) else {}
        table.add_row(escape(path), escape(_short(change.get("before"))),
                      escape(_short(change.get("after"))))
    console.print(table)


def _render_blocked(blocked: list) -> None:
    console.print("\n[bold]refused by the harness[/]")
    if not blocked:
        console.print("  no call was refused.")
        return
    console.print(f"  [red]{len(blocked)}[/] call(s): "
                  + ", ".join(escape(str(b)) for b in blocked))
    console.print("  [dim]a blocked call is the harness working, not the agent "
                  "failing.[/]")


def _render_conversation(run: dict) -> None:
    """How it ended — or that there was no conversation to end.

    THREE elicitation states, and only the middle one is green:

    * **nothing was gated.** ``SimulatedSession.completed`` is *satisfied AND
      nothing still withheld*, so a run the counterparty withheld nothing on is
      "complete" BY CONSTRUCTION — and this printed it green. That is the M40
      vacuity rule inverted at the friendliest possible moment: a check that
      never ran, rendered as a check that passed. It is the common case, not a
      corner: ``scenario/user.py`` excludes a fact whose value is already in the
      opening ticket (``already_in_prompt``), and ``realize()`` interpolates the
      order id into most templates, so most intents gate on nothing at all.
    * **gated and fully elicited** — the agent had to ask and did. A real pass.
    * **gated and still withheld** — it never asked, or the customer left first.

    Whether anything was gated is read off the run's OWN record and not guessed
    at: the gate set is exactly ``disclosed`` ∪ ``withheld`` (``converse()``
    writes ``withheld = held - disclosed``), so both lists empty is the
    counterparty having held nothing back. Why a declared hidden fact did not
    gate is already carried, per fact and with its reason, in ``disclosures``.
    """
    derived = run.get("derived") or {}
    console.print("\n[bold]conversation[/]")
    if not derived.get("conversational"):
        console.print("  [dim]single-shot ticket: no conversation was held. "
                      "`--multi-turn` drives the simulated counterparty.[/]")
        return
    ended = str(run.get("ended") or "")
    gloss = _ENDED_MEANS.get(ended)
    console.print(f"  ended [bold]{escape(ended or 'unrecorded')}[/]"
                  + (f" — {gloss}" if gloss else ""))
    turns = derived.get("n_user_turns")
    console.print("  " + ("[yellow]turns not counted[/] — the trace could not "
                          "be read, so this is unknown rather than zero"
                          if turns is None else
                          f"{turns} counterparty turn(s) reached the agent"))
    elicit = run.get("elicitation") or {}
    disclosed = [str(x) for x in elicit.get("disclosed") or []]
    withheld = [str(x) for x in elicit.get("withheld") or []]
    console.print("  elicited: " + (", ".join(escape(x) for x in disclosed)
                                    or "nothing"))
    console.print("  still withheld: " + (", ".join(escape(x) for x in withheld)
                                          or "nothing"))
    if not disclosed and not withheld:
        console.print("  [yellow]elicitation NOT EXERCISED[/] — this scenario "
                      "gated no fact, so there was nothing for the agent to "
                      "elicit and nothing here could have failed. Not a pass: "
                      "the check never ran. [dim](Why each declared hidden fact "
                      "does not gate is under disclosures.)[/]")
        return
    complete = derived.get("elicitation_complete")
    if complete is None:
        console.print("  [yellow]elicitation completeness not recorded[/] for "
                      "this run — unknown, which is not complete.")
    elif complete:
        console.print(f"  elicitation [green]complete[/] — {len(disclosed)} "
                      "gated fact(s), every one elicited, customer satisfied")
    else:
        console.print("  elicitation [yellow]incomplete[/] — "
                      + (f"{len(withheld)} gated fact(s) the agent never asked "
                         "for" if withheld else
                         "every gated fact was elicited, but the conversation "
                         "did not end satisfied"))


def _render_coverage(coverage: dict) -> None:
    """Which bins this run EXHIBITED — credited from the trace, never from what
    the point asked for.

    Three states kept apart: nothing was collected; it was collected and credited
    nothing; it credited these. The first is not a measurement and the second is,
    and collapsing them is the vacuity rule inverted.
    """
    from agenttic.coverage.model import OTHER_BIN
    console.print("\n[bold]coverage exhibited[/] — credited from the trace, "
                  "never from what was requested")
    if not coverage.get("measured"):
        console.print("  [yellow]no coverage was collected for this run[/] — "
                      "not the same as a run that credited nothing.")
        return
    bins = coverage.get("bins") or []
    if not bins:
        console.print("  measured, and it credited nothing: this run exhibited "
                      "no bin the model names.")
        return
    for b in bins:
        # rpartition, so the token AFTER the last colon is compared whole. A
        # bin named `another` is not the `other` bin, and a substring test here
        # would annotate it as one.
        _, _, bin_id = str(b).rpartition(":")
        note = ("  [dim](the unmodelled bin — outside closure)[/]"
                if bin_id == OTHER_BIN else "")
        console.print(f"  · {escape(str(b))}{note}")


def _corners_never_compared(run: dict) -> list[tuple[str, str]]:
    """Corners the point REQUESTED that neither half of this run's coverage read
    mentions — so nothing in the row says whether the run produced them.

    Derived from the stored row and from nothing else, the way
    ``_faults_view`` re-derives ``never_reached`` instead of trusting a stored
    copy: the two halves a reader can see printed above are ``coverage.bins``
    (exhibited) and ``coverage.divergence`` (asked for, never exhibited), and a
    requested corner named by neither was compared against nothing at all. That
    is a claim about THIS ROW that the reader can check line by line against the
    output — not a guess at which coverage model ran, which the row does not
    record and this function therefore never invents.

    It is not a hypothetical. ``collect()`` records a stimulus hit only for a
    requested dimension the model happens to name and happens to have that bin
    for (``coverage/collect.py``: ``if want and want in cov.bins``), and
    ``divergence()`` additionally skips a coverpoint that is not measurable. On
    the default path both ``scenario run`` and ``agenttic cdv`` read with
    ``baseline_model``, whose own ``BASELINE_LIMITS`` says it "does NOT cover
    intent, emotional register or policy pressure" — three of the five
    dimensions the shipped stimulus point carries. Those three reached no
    comparison, and the empty divergence list they produced was printed as
    *every corner the point requested was exhibited by the run*: a universal
    claim over a set quietly cut to the 40% of the question anything looked at.
    Empty set implies success, which is the one shape this product exists to
    refuse.
    """
    mentioned: set[tuple[str, str]] = set()
    for b in run.get("coverage", {}).get("bins") or []:
        # rpartition for the same reason `_render_coverage` uses it: the token
        # after the LAST colon is the bin, compared whole.
        cp_id, _, bin_id = str(b).rpartition(":")
        mentioned.add((cp_id, bin_id))
    for d in run.get("coverage", {}).get("divergence") or []:
        row = d if isinstance(d, dict) else {}
        mentioned.add((str(row.get("coverpoint_id")), str(row.get("bin_id"))))
    return sorted((str(k), str(v))
                  for k, v in (run.get("point") or {}).items()
                  if (str(k), str(v)) not in mentioned)


def _render_divergence(run: dict) -> None:
    """The corners the point ASKED FOR that the run never produced.

    Read off the STORED row, like everything else these commands print, and
    through ONE renderer both commands call. It used to be printed from the live
    in-memory report by `scenario run` alone and never written down — so the one
    finding this product exists to make could not be read back out of the row
    that claimed to hold the run, and the command that stored it was showing a
    fact the store had dropped.

    THREE states, kept apart the way ``coverage.bins`` keeps its three, and
    ``measured`` speaks for the bins and NOT for this — a row can read
    ``{"measured": true, "bins": [...], "divergence": null}``, so the test here
    is ``divergence is None`` on its own:

    * ``None`` — nobody computed divergence for this run. Not a finding of none.
    * ``[]``   — it WAS computed, and nothing diverged **among the corners it
      compared**.
    * ``[..]`` — the point asked for these and the run did not produce them.

    And a fourth fact that cuts across all three, because the list is silent
    about it and used to be reported as though it were part of it: **which
    corners were compared at all.** See :func:`_corners_never_compared`. An
    empty divergence list is a result about the corners the coverage read could
    speak for and about no others, so this renderer states the size of that set
    (``n of m``) and NAMES the corners outside it rather than closing over them
    with the word "every". A run where all five requested corners were compared
    and a run where two were must not print the same sentence.

    A fact about the GENERATOR's reach and never about the agent's behaviour,
    which is why it is a list of named corners and never a percentage.
    """
    console.print("\n[bold]divergence[/] — what the point requested of the "
                  "generator, measured against what the run produced")
    rows = run.get("coverage", {}).get("divergence")
    if rows is None:
        console.print("  [yellow]divergence was not computed for this run[/] — "
                      "which is not the same claim as nothing having diverged.")
        return

    point = run.get("point") or {}
    never = _corners_never_compared(run)
    n_requested, n_compared = len(point), len(point) - len(never)
    if not rows:
        if not point:
            # A computation that found nothing, over a point this row does not
            # record: "nothing diverged" would be a universal claim over the
            # empty set, which is the same defect one step further out.
            console.print("  computed, and nothing diverged — but this row "
                          "[yellow]records no stimulus point[/], so there is "
                          "nothing here it could have been compared against.")
        elif never:
            console.print(
                f"  computed, and nothing diverged among the "
                f"[bold]{n_compared} of {n_requested}[/] corners the point "
                "requested that this read could speak for.")
        else:
            console.print(
                f"  computed, and nothing diverged: all {n_requested} corners "
                "the point requested were exhibited by the run.")
    for d in rows:
        row = d if isinstance(d, dict) else {}
        console.print(f"  [yellow]asked for, never exhibited[/]: "
                      f"{escape(str(row.get('coverpoint_id', '?')))}="
                      f"{escape(str(row.get('bin_id', '?')))} — the "
                      "point requested that corner and the run did not produce it")
    if not never:
        return
    # Printed under every branch above, including `[rows]`: a divergence list
    # that found something is just as silent about the corners nothing compared,
    # and "these diverged" reads as "and the rest were fine".
    console.print(f"  [yellow]{len(never)} of the {n_requested} corners the "
                  "point requested were never compared to anything[/]:")
    for cp_id, bin_id in never:
        console.print(f"  · [yellow]never compared[/]: {escape(cp_id)}="
                      f"{escape(bin_id)} — neither the exhibited bins nor the "
                      "divergence list above mentions this corner, so nothing "
                      "here says whether the run produced it")
    console.print("  [dim]a dimension the coverage model does not name, has no "
                  "bin for, or cannot measure reaches no comparison — it is "
                  "absent from this read, not clean in it.[/]")


def _render_disclosures(disclosures: list) -> None:
    console.print("\n[bold]disclosures[/] — what this run could not represent "
                  "faithfully")
    if not disclosures:
        console.print("  none recorded.")
        return
    for d in disclosures:
        if isinstance(d, dict):
            kind = escape(str(d.get("kind") or "note"))
            note = escape(str(d.get("note") or d))
        else:
            kind, note = "note", escape(str(d))
        console.print(f"  · [yellow]{kind}[/]: {note}")


def _render_final_answer(trace) -> None:
    console.print("\n[bold]final answer[/]")
    if trace is None:
        console.print("  [yellow]the trace is no longer stored[/], so the "
                      "agent's answer cannot be shown.")
        return
    text = trace.final_output or ""
    console.print(f"  {escape(text)}" if text
                  else "  [yellow]the agent produced no final answer.[/]")


def _render_transcript_section(run: dict) -> None:
    """The conversation this run held — or the sentence saying there was none.

    One renderer, both commands, because `scenario run --multi-turn` is the one
    command that HOLDS a conversation and it was the one command that never
    showed it: it drove the counterparty, stored every turn, and then printed
    the tool calls, the faults, the world diff and how it ended without ever
    printing a word anybody said.

    A single-shot run says so rather than rendering an empty section: "there was
    no conversation" and "the conversation was empty" are different claims.
    """
    console.print("\n[bold]transcript[/]")
    entries = run.get("transcript") or []
    if not entries:
        console.print("  [dim]this run was a single ticket, not a conversation "
                      "— there are no turns to show.[/]")
        return
    _render_transcript(entries)


def _render_transcript(entries: list) -> None:
    """The conversation, turn by turn, with what each turn handed over."""
    for e in entries:
        if e.get("speaker") != "user":
            text = str(e.get("text") or "")
            console.print("\n  [green]agent[/]: "
                          + (escape(text) if text
                             else "[dim](the agent said nothing)[/]"))
            continue
        kind = escape(str(e.get("kind") or "unpaired"))
        head = f"\n  [cyan]customer[/] [dim]({kind})[/]"
        if e.get("delivered") is False:
            head += (" [dim]— the closing turn, never handed to the agent[/]")
        console.print(head + f": {escape(str(e.get('text') or ''))}")
        if e.get("discloses"):
            console.print("    [magenta]discloses[/] "
                          f"{escape(str(e['discloses']))} [dim]— a gated fact "
                          "the agent had to ask for[/]")


def _render_interactions(run: dict) -> None:
    """Escalations and confirmations — what the agent ASKED FOR before acting.

    Stored by the producer and, until now, read by no command. The confirmation
    with a null ``answer`` is the one that matters most: it is the evidence that
    consent was requested and NOT manufactured, which is exactly what
    ``always_irreversible_action_confirmed`` is looking for. An artifact that
    records it and a reader that never shows it leaves the strongest thing in the
    row invisible.
    """
    items = run.get("interactions") or []
    console.print("\n[bold]interactions[/] — what the agent asked for before "
                  "acting")
    if not items:
        console.print("  [dim]none: the agent escalated to nobody and asked for "
                      "no confirmation. On a run that changed the world that is "
                      "a finding; on one that changed nothing it is not.[/]")
        return
    for it in items:
        kind = escape(str(it.get("kind") or "unknown"))
        reason = escape(str(it.get("reason") or ""))
        line = f"  [cyan]{kind}[/]" + (f" — {reason}" if reason else "")
        if "answer" in it:
            ans = it.get("answer")
            line += ("  [yellow](asked, and no answer was recorded — consent was "
                     "requested and not manufactured)[/]" if ans is None
                     else f"  [dim]answered: {escape(str(ans))}[/]")
        console.print(line)


def _render_counterparty_record(run: dict) -> None:
    """The counterparty's OWN record of each turn, beside the transcript join.

    ``turns`` carries four fields the transcript entry drops — ``expect``,
    ``forbid``, ``reason`` and ``source`` — and ``expect``/``forbid`` are the
    entire input to :meth:`UserTurn.grade`. Stored so a run can be re-graded from
    the row rather than re-run; unread, that storage was a promise nothing kept.

    ``None`` is a row written before the record was kept, and is not an empty
    conversation.
    """
    turns = run.get("turns")
    if turns is None:
        console.print("\n[bold]counterparty record[/]")
        console.print("  [dim]this row kept no turn record — not the same as a "
                      "run whose counterparty took no turns.[/]")
        return
    if not turns:
        return                      # single-shot; the conversation block says so
    console.print("\n[bold]counterparty record[/] — what each turn expected of "
                  "the agent, which is what a re-grade reads")
    for i, t in enumerate(turns):
        kind = escape(str((t or {}).get("kind") or "?"))
        bits = []
        for key, gloss in (("expect", "must appear"), ("forbid", "must NOT appear")):
            vals = (t or {}).get(key) or []
            if vals:
                bits.append(f"[magenta]{key}[/] {escape(str(list(vals)))} "
                            f"[dim]({gloss})[/]")
        reason = str((t or {}).get("reason") or "")
        if reason:
            bits.append(f"[dim]ended: {escape(reason)}[/]")
        console.print(f"  turn {i + 1} [dim]({kind})[/]"
                      + ("  " + " · ".join(bits) if bits else
                         "  [dim]nothing was required of the agent here[/]"))


scenario_app = typer.Typer(
    help="Run ONE scenario against the stateful world — and read a past run back.")
app.add_typer(scenario_app, name="scenario")


@scenario_app.command("run")
def scenario_run_cmd(
    agent: str = typer.Option("scenario-agent", "--agent", "-a",
                              help="agent id this run is filed under"),
    seed: int = typer.Option(0, "--seed",
                             help="the draw. Same seed + same space = the same "
                                  "scenario, byte for byte"),
    intent: str = typer.Option("", "--intent",
                               help="pin the intent (refund, exchange, status, "
                                    "complaint, account_change, out_of_scope); "
                                    "empty = drawn from the space"),
    tool_condition: str = typer.Option(
        "", "--tool-condition",
        help="pin the tool condition, which STAGES A FAULT: timeout, error_5xx, "
             "rate_limited, stale_data, malformed_response (or all_ok)"),
    multi_turn: bool = typer.Option(
        False, "--multi-turn/--single-shot",
        help="drive the simulated counterparty, who withholds facts until asked "
             "for them properly"),
    max_turns: int = typer.Option(8, "--max-turns",
                                  help="OUR ceiling; a run that hits it ends "
                                       "turn_cap, reported apart from the "
                                       "customer leaving"),
    space: str = typer.Option(_SCENARIO_SPACE_ID, "--space",
                              help="scenario space id"),
    model: str = typer.Option("", "--model",
                              help="empty (default) = the offline scripted "
                                   "stand-in, no API key, no spend. A model id "
                                   "runs the real client and costs money."),
    config: str = "config.yaml",
):
    """Realize ONE scenario from the space and run it against a world that can
    fail — then store it, so it outlives the process.

    Offline by default: the DUT is a deterministic scripted stand-in, NOT a
    model, and every number below is read off the run it produced. The report
    keeps the distinctions the artifacts keep — a staged fault that never fired
    is printed either as SKIPPED with the reason it could not fire or as NEVER
    REACHED because the agent made no such call, an unchanged world says so in
    words, and the coverage bins are the ones the trace EXHIBITED, never the ones
    the point asked for.
    """
    from agenttic.coverage.collect import Sample, collect
    from agenttic.coverage.models.baseline import baseline_model
    from agenttic.registry.sqlite_store import DuplicateVersionError
    from agenttic.scenario.runner import (
        ScenarioAgent, ScriptedSupportClient, multi_turn_scenario_runner,
        scenario_runner)
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.space import SamplingExhausted, sample_point

    cfg, reg = _ctx(config)
    scenario_space = _scenario_space(reg, space)

    pinned = {k: v for k, v in (("intent", intent),
                                ("tool_condition", tool_condition)) if v}
    try:
        point = sample_point(scenario_space, seed, pinned=pinned)
    except (ValueError, SamplingExhausted) as exc:
        # A pinned value the space does not declare, or a combination its
        # constraints forbid. Both are the operator's typo and neither is worth
        # a traceback.
        raise typer.BadParameter(str(exc)) from None
    scn = realize(point, seed, scenario_space, policy=RETAIL_POLICY)

    if model:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise typer.BadParameter(
                "--model runs a real model and needs ANTHROPIC_API_KEY; leave "
                "--model off to run the offline scripted stand-in.")
        import anthropic
        client, agent_model = anthropic.Anthropic(), model
        console.print(f"[dim]DUT: model {escape(model)} — this run spends "
                      "money.[/]")
    else:
        client, agent_model = ScriptedSupportClient(), "scripted-support"
        console.print("[dim]DUT: the offline scripted stand-in — deterministic, "
                      "keyless, and NOT a model.[/]")
    adapter = ScenarioAgent(model=agent_model, client=client, agent_id=agent,
                            max_steps=int(cfg["harness"]["max_steps"]))

    run_one = (multi_turn_scenario_runner(max_turns=max_turns) if multi_turn
               else scenario_runner())
    outcome = run_one(scn, adapter=adapter, store=reg)

    # Coverage from the SAME model and the same collector the scorecard uses, so
    # this run's bins and a suite's bins are one computation. `classify=None`
    # keeps it free and offline: classifier-backed bins stay unevaluated rather
    # than being credited by a guess.
    # Bound once and stored beside the bins: a bin id is only interpretable
    # against the model that names it.
    cov_model = baseline_model(cfg=cfg)
    report = collect(cov_model,
                     [Sample(trace=outcome.trace, scenario=scn.as_dict(),
                             requested=dict(point))])
    # `countable()` + `exhibited()` rather than the raw `trace_hits` counter:
    # they are where the coverage model's measurability actually lands, and the
    # raw counter routes around it. `session_shape` is declared measurable=False
    # ("a trace with no turn markers is evidence of absent instrumentation, not
    # of a single-turn session") and its `single_turn` extractor is
    # `_human_turns(trace) <= 1`, which is True at ZERO turns — so a single-shot
    # run scored `session_shape:single_turn` off a trace with no `user_turn`
    # span at all, and the bin then flowed into the store and both UI panels
    # under the words "credited from what the run EXHIBITED". `report`
    # already holds that dimension out of `trace_closure`; this makes the stored
    # bins agree with it. `exhibited()` also picks up per-sample gating
    # (`measurable_when`), which `trace_hits` cannot see.
    bins = sorted(f"{cp_id}:{b.bin_id}"
                  for cp_id, cov in report.coverpoints.items()
                  for b in cov.countable() if cov.exhibited(b))

    # `divergence` is stored beside the bins because it is the other half of the
    # same read and the half that carries the finding. Passed VERBATIM — the
    # rows `CoverageReport.divergence()` returned for THIS run's sample, each one
    # that method's own dict — and never `or []`: an empty list is a computation
    # that found nothing diverged, `None` is nobody having computed it, and the
    # store keeps the two apart on purpose.
    try:
        run_id = reg.save_scenario_run(scn, outcome, exhibited_bins=bins,
                                       divergence=report.divergence(),
                                       coverage_model=cov_model)
    except DuplicateVersionError as exc:
        console.print(f"[red]not stored[/]: {escape(str(exc))}")
        raise typer.Exit(1) from None

    # Everything below is rendered from the STORED run and the STORED trace,
    # through the same renderers `scenario transcript` uses. Two consequences,
    # both wanted: what is printed here is exactly what a reader gets back later,
    # and a fact the store dropped cannot be shown by the command that stored it.
    stored = reg.get_scenario_run(run_id)
    trace = reg.get_trace(stored["trace_id"])   # save_scenario_run proved it exists
    _render_run_header(stored)
    _render_transcript_section(stored)
    _render_tool_calls(trace)
    _render_final_answer(trace)
    _render_faults(stored["faults"])
    _render_state_diff(stored["state_diff"])
    _render_blocked(stored["blocked"])
    _render_conversation(stored)
    _render_counterparty_record(stored)
    _render_interactions(stored)
    _render_coverage(stored["coverage"])
    # The whole row: divergence is a statement about the POINT, and the point is
    # the half of it the coverage dict does not carry.
    _render_divergence(stored)
    _render_disclosures(stored["disclosures"])
    console.print(f"\nstored as run [bold]{escape(run_id)}[/] — read it back: "
                  f"agenttic scenario transcript {escape(run_id)}")


@scenario_app.command("transcript")
def scenario_transcript_cmd(
    run_id: str = typer.Argument(..., help="run id (see `agenttic scenario list`)"),
    config: str = "config.yaml",
):
    """Read a stored run back, turn by turn.

    A single-shot run says so rather than printing an empty conversation: "no
    battery was run" and "the battery measured nothing" are different claims and
    must look different.
    """
    from agenttic.registry.sqlite_store import NotFoundError
    _, reg = _ctx(config)
    try:
        run = reg.get_scenario_run(run_id)
    except NotFoundError:
        raise typer.BadParameter(
            f"no scenario run {run_id!r} in this tenant — `agenttic scenario "
            "list` shows what is stored.") from None
    try:
        trace = reg.get_trace(run["trace_id"])
    except NotFoundError:
        # Reported, never guessed at: the run's own disclosures already carry
        # this, and the renderers say "unknown" where they would have said 0.
        trace = None

    _render_run_header(run)
    _render_transcript_section(run)
    _render_final_answer(trace)
    _render_conversation(run)
    _render_counterparty_record(run)
    _render_interactions(run)
    _render_tool_calls(trace)
    _render_faults(run["faults"])
    _render_state_diff(run["state_diff"])
    _render_blocked(run["blocked"])
    _render_coverage(run["coverage"])
    _render_divergence(run)
    _render_disclosures(run["disclosures"])


@scenario_app.command("list")
def scenario_list_cmd(
    scenario_id: str = typer.Option("", "--scenario",
                                    help="only runs of this scenario id"),
    agent: str = typer.Option("", "--agent", "-a", help="only this agent's runs"),
    limit: int = typer.Option(20, "--limit", help="most recent N"),
    config: str = "config.yaml",
):
    """Recent scenario runs, newest first."""
    _, reg = _ctx(config)
    # ONE row past the page, then dropped. `len(runs)` is the page size and was
    # printed as though it were the number stored: a capped list that reads as a
    # complete one is a false claim about how much evidence exists, and it is the
    # claim an operator makes decisions against ("only 20 runs? then nothing else
    # ran"). Asking for limit+1 answers "is there more" from the store rather
    # than from a guess, and without inventing a total nobody counted.
    n = max(1, int(limit))
    fetched = reg.list_scenario_runs(scenario_id=scenario_id or None,
                                     agent_id=agent or None, limit=n + 1)
    runs, more = fetched[:n], len(fetched) > n
    if not runs:
        # Found beside the defect above and the same family: a zero UNDER A
        # FILTER is a measurement — "nothing matched this query" — and printing
        # the empty-store sentence for it is a false claim about the store, made
        # in the one place a reader goes to find out how much evidence exists.
        narrowed = ", ".join(f for f in (
            f"--scenario {scenario_id}" if scenario_id else "",
            f"--agent {agent}" if agent else "") if f)
        if narrowed:
            console.print(f"No scenario run matches [bold]{escape(narrowed)}[/]"
                          " — which is not the same claim as no run being "
                          "stored. Drop the filter to see what is.")
        else:
            console.print("No scenario runs stored. Run one: "
                          "[bold]agenttic scenario run[/]")
        return
    table = Table()
    # TWO columns, not seven. The run id is the one cell a reader has to COPY,
    # so it never wraps and is never ellipsized — and seven narrow columns is
    # exactly what forces rich to break `demo-bot` across two lines, which makes
    # a value someone has to read into a puzzle. The rest is one sentence per
    # run, word-wrapped, so every token stays whole at any width.
    table.add_column("run id", no_wrap=True)
    table.add_column("what happened")
    for r in runs:
        faults = r.get("faults") or {}
        counts = faults.get("counts")
        if not faults.get("recorded"):
            # Never "0 fired": a run that stored no report did not stage nothing.
            fired = "[dim]fault report not recorded[/]"
        elif counts is None:
            # THREE states, not two. `_faults_view` returns `recorded: True` with
            # `counts: None` for a report that IS stored and could not be
            # reconstructed on read (it names a tool the world does not have,
            # say) — and it keeps that apart from `recorded: False` on purpose.
            # Printing it as "not recorded" is a false claim about the record,
            # it merges two different absences, and it says the opposite of what
            # `scenario transcript` prints for the same run.
            fired = "[yellow]fault report could not be reconstructed[/]"
        elif not counts["planned"]:
            # A recorded plan of nothing. Distinct from "not recorded" above and
            # from "0 fired" below, which would imply something was staged.
            fired = "no fault staged"
        else:
            # FOUR fates, not a ratio. `0/1 fired` is byte-identical for a fault
            # that reached its call and could not happen (`skipped`) and one the
            # agent never got to (`never_reached`) — two different facts about
            # the harness, collapsed in the one place an operator scans. The
            # detail view has always kept them apart; the list quietly did not.
            fates = [f"{counts['fired']}/{counts['planned']} fired"]
            if counts.get("skipped"):
                fates.append(f"{counts['skipped']} skipped")
            if counts.get("never_reached"):
                fates.append(f"{counts['never_reached']} never reached")
            fired = "faults " + ", ".join(fates)
        shape = (f"conversation · {escape(r['ended'] or 'unrecorded')}"
                 if r["conversational"] else "single-shot")
        world = ("[yellow]world changed[/]" if r["world_changed"]
                 else "world unchanged")
        table.add_row(escape(r["run_id"]),
                      f"{escape(r['agent_id'])} · {shape} · {world} · "
                      f"{r['n_blocked']} blocked · {fired} · "
                      f"{escape(str(r['created_at']).replace('T', ' ')[:16])} "
                      "UTC")
    console.print(table)
    console.print(f"[dim]{len(runs)} run(s) shown[/]"
                  + (f" — [yellow]the list is CAPPED at --limit {n}[/][dim] and "
                     "there are older runs it does not show"
                     if more else "[dim] — the complete list for this filter")
                  + ". Read one: agenttic scenario transcript <run id> — which "
                  "also prints the scenario id `--scenario` filters on.[/]")


standard_app = typer.Typer(help="Canonical standard benchmark suites + metrics.")
app.add_typer(standard_app, name="standard")


@standard_app.command("seed")
def standard_seed(config: str = "config.yaml"):
    """Install the canonical standard suites (tool-use + safety) — idempotent."""
    from agenttic.metrics.standard_suites import seed_standard_suites
    _, reg = _ctx(config)
    added = seed_standard_suites(reg)
    console.print(f"[green]Seeded[/] {len(added)} standard suite(s): "
                  f"{', '.join(added) or '(already present)'}")


@standard_app.command("run")
def standard_run(
    agent: str = typer.Option("standard-agent", "--agent", "-a", help="agent id (label)"),
    k: int = typer.Option(3, "--k", help="repeated runs per case for pass^k (cost is k x)"),
    system_prompt: str = "", url: str = "", config: str = "config.yaml"):
    """Run the canonical suites k times for an agent and record the Agenttic Index
    (incl. pass^k + ECE). Needs ANTHROPIC_API_KEY. NOTE: k runs cost k x tokens."""
    cfg, reg = _ctx(config)
    variant = "blackbox" if url else "reference"
    res = asyncio.run(ops.run_standard_op(cfg, reg, agent_id=agent, k=k,
                                          variant=variant, url=url,
                                          system_prompt=system_prompt))
    console.print(f"[bold]Agenttic Index {res['index']}[/]  (agent {agent}, k={k}, "
                  f"{res['n_cases']} cases, cost ${res['k_runs_cost_usd']:.4f})")
    for mid, v in res["components"].items():
        console.print(f"  {res['names'].get(mid, mid)}: {v}")
    console.print(f"  calibration mode: {res['calibration_mode']}")


@standard_app.command("ingest")
def standard_ingest(dataset: str = typer.Argument("bfcl", help="dataset id (e.g. bfcl)"),
                    full: bool = typer.Option(False, "--full",
                        help="fetch the full split from the source (else vendored sample)"),
                    config: str = "config.yaml"):
    """Ingest a real public dataset into a labeled standard suite (e.g. BFCL)."""
    from agenttic.metrics.datasets import get_adapter
    _, reg = _ctx(config)
    try:
        res = get_adapter(dataset).ingest(reg, full=full)
    except KeyError as exc:
        raise typer.BadParameter(str(exc))
    if res.get("already_present"):
        console.print(f"[yellow]{res['suite_id']}[/] already ingested.")
    else:
        console.print(f"[green]Ingested[/] {res['ingested']} {dataset} cases into "
                      f"{res['suite_id']} ({res['license']}).")


@standard_app.command("metrics")
def standard_metrics():
    """List the canonical metrics, the methodology each implements, and weights."""
    from agenttic.metrics.catalog import METRICS
    table = Table("metric", "category", "weight", "methodology")
    for m in METRICS:
        w = f"{m.weight:.3f}" + ("" if m.status == "implemented" else " (deferred)")
        table.add_row(m.name, m.category, w, m.methodology[:70] + "…")
    console.print(table)


# --------------------------------------------------------------------------- #
# Certification profiles (SPEC-2 M4).
# --------------------------------------------------------------------------- #
profiles_app = typer.Typer(help="Certification profiles: pinned recipes + coverage.")
app.add_typer(profiles_app, name="profiles")


@profiles_app.command("list")
def profiles_list(config: str = "config.yaml"):
    """List certification profiles defined in config."""
    cfg, _reg = _ctx(config)
    defined = (cfg.get("certification", {}) or {}).get("profiles", {})
    if not defined:
        console.print("[dim]no certification profiles defined[/]")
        return
    table = Table("profile", "min_k", "required domains")
    for pid, pc in defined.items():
        table.add_row(pid, str(pc.get("min_k", 1)),
                      str(len(pc.get("required_domains", []))))
    console.print(table)


@profiles_app.command("show")
def profiles_show(
    profile_id: str = typer.Argument(..., help="profile id, e.g. cert-agent-safety-v1"),
    config: str = "config.yaml",
):
    """Show a profile's composition, pinned suite versions, coverage table, and
    caveats (verbatim). Seeds the standard suites first so coverage is populated."""
    from agenttic.certification.coverage import coverage
    from agenttic.certification.profiles import ProfileError, build_profile
    from agenttic.metrics.standard_suites import seed_standard_suites
    cfg, reg = _ctx(config)
    seed_standard_suites(reg)
    try:
        profile = build_profile(cfg, reg, profile_id)
    except ProfileError as exc:
        raise typer.BadParameter(str(exc))

    console.print(f"[bold]{profile.profile_id}[/] v{profile.version}  "
                  f"(min_k={profile.min_k})")
    if profile.thresholds:
        console.print("[dim]thresholds:[/] " + ", ".join(
            f"{k}={v}" for k, v in profile.thresholds.items()))

    pins = Table("pinned suite", "version")
    for ref in profile.suite_refs:
        pins.add_row(ref.suite_id, str(ref.version))
    console.print(pins)

    cov = Table("domain", "coverage", "evidence")
    for c in coverage(reg, profile):
        label = ("[red]NOT ASSESSED[/]" if c.status == "not_assessed"
                 else f"[yellow]{c.status}[/]" if c.status == "assessed_seed"
                 else f"[green]{c.status}[/]")
        cov.add_row(c.domain, label, ", ".join(c.evidence_refs) or "—")
    console.print(cov)

    if profile.caveats:
        console.print("[bold]Caveats:[/]")
        for cav in profile.caveats:
            console.print(f"  • {cav}")


oversight_app = typer.Typer(help="Interactive oversight loop (opt-in).")
app.add_typer(oversight_app, name="oversight")


@oversight_app.command("watch")
def oversight_watch(
    agent: str = typer.Option("", "--agent", "-a", help="filter by agent id"),
    config: str = "config.yaml",
):
    """Watch pending oversight reviews + loosening proposals (from the append-only
    enforcement log). The loop is opt-in (oversight.interactive_loop.enabled)."""
    from agenttic.enforce.interactive_oversight import (
        pending_loosen_proposals,
        pending_reviews,
    )
    cfg, reg = _ctx(config)
    enabled = (cfg.get("oversight", {}).get("interactive_loop", {}) or {}).get(
        "enabled", False)
    console.print(f"interactive oversight loop: "
                  f"{'[green]ENABLED[/]' if enabled else '[dim]disabled[/]'}")
    reviews = pending_reviews(reg, agent or None)
    if reviews:
        table = Table("review_id", "pattern", "reasons", "options")
        for r in reviews:
            table.add_row(r.get("review_id", ""), r.get("pattern", ""),
                          ", ".join(r.get("reasons", [])),
                          " | ".join(r.get("options", [])))
        console.print("[bold]Pending reviews:[/]")
        console.print(table)
    else:
        console.print("[dim]no pending reviews[/]")
    proposals = pending_loosen_proposals(reg, agent or None)
    if proposals:
        console.print("[bold yellow]Loosening proposals (need confirmation):[/]")
        for p in proposals:
            console.print(f"  {p.get('proposal_id')} — pattern {p.get('pattern')} "
                          f"(feedback: {len(p.get('feedback_ids', []))})")


@oversight_app.command("confirm")
def oversight_confirm(
    agent: str = typer.Argument(..., help="agent id"),
    proposal_id: str = typer.Argument(..., help="loosening proposal id"),
    config: str = "config.yaml",
):
    """Explicitly confirm a loosening proposal (the only way loosening is ever
    applied). Records the confirmation and applies it."""
    from agenttic.enforce.interactive_oversight import InteractiveOversightLoop
    cfg, reg = _ctx(config)
    loop = InteractiveOversightLoop(reg, cfg)
    try:
        result = loop.confirm_loosening(agent, proposal_id, "cli")
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    if result.get("applied"):
        console.print(f"[green]Loosening applied[/] for {result['pattern']} "
                      f"(policy {result['policy_hash'][:12]}…)")
    else:
        console.print(f"[yellow]Confirmed[/] — {result.get('reason', 'no change')}")


cards_app = typer.Typer(help="Agent cards: autofill, show, annotate.")
app.add_typer(cards_app, name="cards")


@cards_app.command("autofill")
def cards_autofill(
    agent: str = typer.Argument(..., help="agent id"),
    config: str = "config.yaml",
):
    """Autofill a card from Agenttic's own measured evidence and persist it."""
    from agenttic.cards.agency import detect_covered_agent
    from agenttic.cards.autofill import autofill_card
    from agenttic.cards.autonomy import classify_autonomy
    cfg, reg = _ctx(config)
    card = autofill_card(cfg, reg, agent)
    aut = classify_autonomy(reg, agent, cfg)
    fv = aut.to_field_value()
    if fv is not None:
        card.fields[fv.field_key] = fv
    reg.save_card(card)
    cov = detect_covered_agent(reg, agent, cfg)
    console.print(f"[green]Autofilled[/] card for {agent} "
                  f"({len(card.present_fields())} measured fields)")
    console.print(f"  autonomy: {aut.level or 'None'} ({aut.label or '—'})")
    console.print(f"  covered agent: {cov.covered}")


@cards_app.command("show")
def cards_show(
    agent: str = typer.Argument(...),
    config: str = "config.yaml",
):
    """Show the latest card for an agent (field key → status/provenance)."""
    from agenttic.registry.sqlite_store import NotFoundError
    _cfg, reg = _ctx(config)
    try:
        card = reg.get_card(agent)
    except NotFoundError:
        raise typer.BadParameter(f"no card for {agent} (run `agenttic cards autofill`)")
    table = Table("field", "status", "provenance", "refs")
    for key, fv in card.fields.items():
        refs = ", ".join((fv.evidence_refs or fv.citations)[:2]) or "—"
        table.add_row(key, fv.status, fv.provenance or "—", refs)
    console.print(f"[bold]card {agent}[/] v{card.version} (source {card.source})")
    console.print(table)


@cards_app.command("annotate")
def cards_annotate(
    agent: str = typer.Argument(...),
    field: str = typer.Option(..., "--field", "-f", help="field key"),
    value: str = typer.Option(..., "--value", "-v"),
    citation: list[str] = typer.Option([], "--citation", "-c",
                                       help="citation URL (required for documented)"),
    config: str = "config.yaml",
):
    """Add a DOCUMENTED field value. Rejects documented values without citations."""
    from agenttic.registry.sqlite_store import NotFoundError
    from agenttic.schema.agent_card import AgentCard, FieldValue
    _cfg, reg = _ctx(config)
    if not citation:
        raise typer.BadParameter(
            "documented values require at least one --citation (Hard Rule 16)")
    try:
        card = reg.get_card(agent)
    except NotFoundError:
        card = AgentCard(agent_id=agent, source="agenttic")
    try:
        fv = FieldValue.documented(field, value, list(citation))
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    card.fields[field] = fv
    reg.save_card(card)
    console.print(f"[green]Annotated[/] {agent}.{field} (documented, "
                  f"{len(citation)} citation(s))")


incidents_app = typer.Typer(help="Safety incidents: open, triage, report, close.")
app.add_typer(incidents_app, name="incidents")


@incidents_app.command("list")
def incidents_list(
    agent: str = typer.Option("", "--agent", "-a", help="filter by agent id"),
    config: str = "config.yaml",
):
    """List incidents with computed state + SLA due clock + overdue flag."""
    from agenttic.live.incidents import IncidentManager
    cfg, reg = _ctx(config)
    rows = IncidentManager(reg).list_with_sla(cfg, agent_id=agent or None)
    if not rows:
        console.print("[dim]no incidents[/]")
        return
    table = Table("id", "agent", "sev", "state", "origin", "due", "overdue")
    for r in rows:
        overdue = "[red]OVERDUE[/]" if r["overdue"] else ""
        table.add_row(r["incident_id"], r["agent_id"], r["severity"],
                      r["state"], r["origin"], r["sla_due"][:16], overdue)
    console.print(table)


@incidents_app.command("open")
def incidents_open(
    agent: str = typer.Argument(..., help="agent id"),
    severity: str = typer.Option("S3", "--severity", "-s", help="S1|S2|S3|S4"),
    title: str = typer.Option("", "--title", "-t"),
    summary: str = typer.Option("", "--summary"),
    config: str = "config.yaml",
):
    """Manually open an incident."""
    from agenttic.live.incidents import open_manual
    _cfg, reg = _ctx(config)
    inc = open_manual(reg, agent_id=agent, severity=severity, title=title,
                      summary=summary)
    console.print(f"[green]Opened[/] {inc.incident_id} ({severity}) for {agent}")


@incidents_app.command("report")
def incidents_report(
    incident_id: str = typer.Argument(...),
    note: str = typer.Option("", "--note", "-n"),
    config: str = "config.yaml",
):
    """Move an incident to the 'reported' state (must be triaged first)."""
    from agenttic.live.incidents import IllegalTransitionError, IncidentManager
    _cfg, reg = _ctx(config)
    m = IncidentManager(reg)
    try:
        if m.current_state(incident_id) == "open":
            m.transition(incident_id, "triaged", actor="cli", note=note)
        inc = m.transition(incident_id, "reported", actor="cli", note=note)
    except IllegalTransitionError as exc:
        raise typer.BadParameter(str(exc))
    console.print(f"[green]Reported[/] {inc.incident_id} (state {inc.state})")


@incidents_app.command("close")
def incidents_close(
    incident_id: str = typer.Argument(...),
    note: str = typer.Option("", "--note", "-n"),
    config: str = "config.yaml",
):
    """Close an incident."""
    from agenttic.live.incidents import IllegalTransitionError, IncidentManager
    _cfg, reg = _ctx(config)
    try:
        inc = IncidentManager(reg).transition(incident_id, "closed",
                                              actor="cli", note=note)
    except IllegalTransitionError as exc:
        raise typer.BadParameter(str(exc))
    console.print(f"[green]Closed[/] {inc.incident_id}")


@incidents_app.command("export")
def incidents_export(
    incident_id: str = typer.Argument(...),
    config: str = "config.yaml",
):
    """Print the regulator-facing JSON export for an incident."""
    import json as _json

    from agenttic.live.incidents import IncidentManager
    cfg, reg = _ctx(config)
    inc = IncidentManager(reg).get(incident_id)
    console.print_json(_json.dumps(inc.export(cfg)))


@app.command()
def init(
    directory: str = typer.Argument(".", help="target directory (default: current)"),
    target: str = typer.Option("", "--target",
                               help="where traces go (e.g. https://your-agenttic/v1/traces); "
                                    "blank => offline quickstart"),
    force: bool = typer.Option(False, "--force", help="overwrite existing files"),
):
    """Scaffold a runnable quickstart (config + reference KB + sample + steps).

    In an empty directory this yields a working setup that certifies the built-in
    reference agent with no further edits and no API key:

        agenttic init
        agenttic certify --mock
    """
    from agenttic.release.scaffold import scaffold
    res = scaffold(directory, target=target, force=force)
    for name in res["written"]:
        console.print(f"[green]created[/] {name}")
    for name in res["skipped"]:
        console.print(f"[yellow]exists, skipped[/] {name} [dim](use --force to overwrite)[/]")
    console.print(f"\n[bold]Scaffolded[/] {res['dest']}")
    console.print("Next — get a signed grade in under a minute (no API key):")
    console.print("  [cyan]agenttic certify --mock --out dossier.json[/]")
    console.print("  [cyan]agenttic dossier verify dossier.json[/]")
    console.print("Then trace your own agent — see [bold]agent_sample.py[/] and "
                  "[bold]QUICKSTART.md[/].")


@app.command()
def doctor(
    target: str = typer.Option("", "--target",
                               help="ingest URL to probe (e.g. https://your-agenttic/v1/traces)"),
    spans: str = typer.Option("", "--spans",
                              help="a captured OTLP JSON file to validate offline"),
    auth_header: str = typer.Option("", "--auth-header", help="Authorization header for the probe"),
):
    """Verify zero-touch OTel setup: confirm spans arrive at a target and/or that
    a captured span stream parses into a canonical run."""
    import json as _json

    from agenttic.ingest.doctor import diagnose_payload, probe_target
    if not target and not spans:
        raise typer.BadParameter("provide --target URL and/or --spans FILE")

    failed = False

    if spans:
        try:
            payload = _json.loads(Path(spans).read_text())
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]FAIL[/] could not read {spans}: {e}")
            raise typer.Exit(1)
        rep = diagnose_payload(payload)
        if rep["ok"]:
            console.print(
                f"[green]OK[/] parsed {rep['spans']} span(s) → {rep['traces']} "
                f"trace(s): {rep['llm_calls']} llm_call, {rep['tool_calls']} "
                f"tool_call, {rep['incomplete']} incomplete "
                f"[dim](agents: {', '.join(rep['agents']) or '—'})[/]")
        else:
            console.print(f"[red]FAIL[/] span stream {spans}")
        for p in rep["problems"]:
            console.print(f"  • {p}")
        failed |= not rep["ok"]

    if target:
        rep = probe_target(target, auth_header=auth_header or None)
        if rep["ok"]:
            console.print(f"[green]OK[/] {target} is reachable and parses OTLP "
                          "spans — zero-touch setup is live.")
        else:
            console.print(f"[red]FAIL[/] probing {target}")
        for p in rep["problems"]:
            console.print(f"  • {p}")
        failed |= not rep["ok"]

    if failed:
        raise typer.Exit(1)


@app.command()
def certify(
    agent: str = typer.Option("ref-agent", "--agent", "-a", help="agent id (label)"),
    profile: str = typer.Option("cert-agent-safety-v1", "--profile", "-p",
                                help="certification profile id"),
    out: str = typer.Option("", "--out", "-o", help="write the dossier JSON here"),
    url: str = typer.Option("", "--url", help="black-box agent endpoint (else reference)"),
    system_prompt: str = typer.Option("", "--system-prompt"),
    renew: bool = typer.Option(False, "--renew", help="renew (chained dossier; $0 if unchanged)"),
    mock: bool = typer.Option(False, "--mock", help="offline deterministic provider (no API key)"),
    config: str = "config.yaml",
):
    """Certify an agent against a profile → an evidence dossier (Tier A/B/C).

    Provisional judge ⇒ tier ≤ B. Cache-aware: an identical agent config + profile
    is served for $0. --renew emits a chained dossier ($0 if unchanged). Use
    --mock for an offline, no-key run."""
    import asyncio

    from agenttic.certification.certify import certify as _certify
    from agenttic.certification.certify import renew as _renew
    from agenttic.reporting.dossier_report import render_json
    cfg, reg = _ctx(config)
    variant = "blackbox" if url else "reference"
    client = None
    if mock:
        from agenttic.certification.mock_provider import MockAnthropicClient
        client = MockAnthropicClient()
    op = _renew if renew else _certify
    res = asyncio.run(op(cfg, reg, agent_id=agent, profile_id=profile,
                         variant=variant, url=url, system_prompt=system_prompt,
                         client=client, judge_client=client))
    d = res.dossier
    tag = "[dim](cached, $0)[/]" if res.cached else f"[dim](${res.cost_usd:.4f})[/]"
    console.print(f"[bold]Dossier {d.dossier_id}[/] — Tier [bold]{d.tier_decision.tier}[/] "
                  f"{tag}")
    if d.tier_decision.caps_applied:
        console.print("[yellow]Caps:[/] " + ", ".join(d.tier_decision.caps_applied))
    for c in d.coverage:
        if c.status == "not_assessed":
            console.print(f"  [red]NOT ASSESSED[/] {c.domain}")
    if out:
        Path(out).write_text(render_json(d))
        console.print(f"[green]Wrote[/] {out}")


dossier_app = typer.Typer(help="Certification dossiers: verify, inspect.")
app.add_typer(dossier_app, name="dossier")


@dossier_app.command("verify")
def dossier_verify(
    target: str = typer.Argument(..., help="dossier JSON path or dossier id"),
    config: str = "config.yaml",
):
    """Recompute the dossier's hashes offline; names the offending ref on mismatch."""
    from agenttic.certification.dossier import verify
    reg = None
    try:
        _cfg, reg = _ctx(config)
    except Exception:  # noqa: BLE001 — verify works offline from a path alone
        reg = None
    res = verify(target, reg=reg)
    if res.ok:
        console.print(f"[green]VERIFIED[/] dossier {res.dossier_id} — Tier {res.tier}")
    else:
        console.print(f"[red]FAILED[/] dossier {res.dossier_id}")
        for p in res.problems:
            console.print(f"  • {p}")
        raise typer.Exit(1)


@dossier_app.command("revoke")
def dossier_revoke(
    dossier_id: str = typer.Argument(...),
    reason: str = typer.Option(..., "--reason", help="why this dossier is revoked"),
    config: str = "config.yaml",
):
    """Revoke a dossier (append-only). The dossier stays readable; its status
    flips to 'revoked'. There is no un-revoke / manual-promotion path."""
    from agenttic.certification.dossier import revoke
    from agenttic.registry.sqlite_store import NotFoundError
    _cfg, reg = _ctx(config)
    try:
        revoke(reg, dossier_id, reason=reason)
    except NotFoundError:
        raise typer.BadParameter(f"dossier {dossier_id} not found")
    console.print(f"[red]REVOKED[/] dossier {dossier_id} — {reason}")


@dossier_app.command("show")
def dossier_show(
    dossier_id: str = typer.Argument(...),
    fmt: str = typer.Option("md", "--format", "-f", help="md|json|inspect"),
    config: str = "config.yaml",
):
    """Render a persisted dossier (md/json/inspect) with its computed status."""
    from agenttic.certification.staleness import status, status_reasons
    from agenttic.reporting.dossier_report import render
    _cfg, reg = _ctx(config)
    d = reg.get_dossier(dossier_id)
    st = status(reg, d)
    color = {"current": "green", "stale": "yellow", "revoked": "red"}.get(st, "dim")
    console.print(f"[{color}]status: {st}[/]"
                  + (f" — {'; '.join(status_reasons(reg, d))}"
                     if st != "current" else ""))
    console.print(render(d, fmt))


# --- airgap: no-egress self-check (SPEC-7 Step 38) -------------------------
airgap_app = typer.Typer(help="Air-gapped mode: no-egress self-check.")
app.add_typer(airgap_app, name="airgap")


@airgap_app.command("check")
def airgap_check(config: str = "config.yaml"):
    """Audit the config for egress-requiring capabilities (Step 38 self-check).

    Exits non-zero if air-gap mode is on and any blocking capability is enabled —
    the same gate the server runs at startup."""

    from agenttic.airgap import egress_self_check
    cfg = load_config(config)
    rep = egress_self_check(cfg)
    console.print(f"air-gap mode: [{'green' if rep['enabled'] else 'yellow'}]"
                  f"{'ON' if rep['enabled'] else 'off'}[/]")
    if rep["unavailable"]:
        console.print("[dim]egress-only features unavailable offline:[/]")
        for u in rep["unavailable"]:
            console.print(f"  - {u['name']}: {u['detail']}")
    if rep["offenders"]:
        console.print(f"[red]{len(rep['offenders'])} egress offender(s):[/]")
        for o in rep["offenders"]:
            console.print(f"  [red]✗[/] {o['name']}: {o['detail']}")
        if rep["enabled"]:
            console.print("[red]air-gap self-check FAILED — server would refuse to boot.[/]")
            raise typer.Exit(code=1)
    else:
        console.print("[green]no egress offenders.[/]")


# --- enforce: progressive enforcement ramp (SPEC-7 Step 39) ----------------
enforce_app = typer.Typer(help="Progressive enforcement ramp (observe→enforce).")
app.add_typer(enforce_app, name="enforce")


@enforce_app.command("mode")
def enforce_mode(
    agent: str = typer.Argument(..., help="agent id"),
    mode: str = typer.Argument("show",
        help="observe|shadow|enforce_reads|enforce_all (omit to show current)"),
    actor: str = typer.Option("cli", "--actor", help="who is making the change"),
    config: str = "config.yaml",
):
    """Set (or, with no mode, show) an agent's enforcement mode.

    Advancing is deliberate; stepping down to observe is always allowed (safety
    valve). A mode change never loosens the compiled policy."""
    from agenttic.enforce import ramp
    _cfg, reg = _ctx(config)
    if mode.lower() in ("show", "current", ""):
        console.print(f"{agent}: [cyan]{ramp.current_mode(reg, agent)}[/]")
        return
    try:
        res = ramp.set_mode(reg, agent, mode.lower(), actor)
    except ramp.RampError as e:
        raise typer.BadParameter(str(e))
    arrow = {"advance": "↑", "step_down": "↓", "noop": "="}[res["direction"]]
    console.print(f"{agent}: {res['from']} {arrow} [cyan]{res['to']}[/] "
                  f"(by {res['actor']})")


@enforce_app.command("shadow-report")
def enforce_shadow_report(
    agent: str = typer.Argument(..., help="agent id"),
    config: str = "config.yaml",
):
    """Show the would-be-block report: what shadow mode *would* have blocked, the
    projected impact of enforcing, and false-positive candidates."""
    from agenttic.enforce import ramp
    _cfg, reg = _ctx(config)
    rep = ramp.shadow_report(reg, agent)
    console.print(f"[bold]{agent}[/] — mode [cyan]{rep['mode']}[/]")
    console.print(f"would-be blocks: [yellow]{rep['would_be_blocks']}[/] "
                  f"of {rep['total_decisions']} decisions "
                  f"(projected block rate {rep['projected_block_rate']})")
    if rep["by_tool"]:
        console.print("by tool: " + ", ".join(f"{k}={v}" for k, v in rep["by_tool"].items()))
    console.print(f"false-positive candidates: {rep['fp_candidate_count']}")


# --- ingest: OTel-GenAI span import (SPEC-7 Step 35) -----------------------
ingest_app = typer.Typer(help="Ingest traces from an external OTel bus.")
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("otel")
def ingest_otel(
    file: str = typer.Argument(..., help="path to an OTLP span dump (JSON)"),
    config: str = "config.yaml",
):
    """Import exported OTel-GenAI spans as live traces (source=otel_ingest).

    Ingested traces are stored as mode='live' and are structurally excluded from
    batch certification scorecards (SPEC-1 Step 9 invariant)."""
    from agenttic.ingest.mapping import ingest_spans
    from agenttic.ingest.otel import load_span_dump
    _cfg, reg = _ctx(config)
    spans = load_span_dump(file)
    rep = ingest_spans(reg, spans)
    console.print(
        f"[green]Ingested[/] {rep['trace_count']} trace(s), "
        f"{rep['decision_count']} decision(s) from {len(spans)} span(s).")
    if rep["incomplete_spans"]:
        console.print(f"[yellow]{len(rep['incomplete_spans'])} incomplete span(s)[/] "
                      "(partial traces, no fabricated fields).")
    for tid in rep["saved_trace_ids"]:
        console.print(f"  live trace {tid}")
    console.print("[dim]→ measure closure over this traffic: "
                  "agenttic ingest verify-traffic --agent <id>[/]")


def _closure_cell(d: dict) -> str:
    """One coverpoint's closure as a console cell — three states, three texts.

    The rich-console twin of ``reporting.scorecard_report._closure_cell``: same
    rule, different output language. A dimension nothing in the system can feed
    is never printed as a percentage (that would be the over-report) and never as
    ``0%`` (zero is a measurement — "the suite never got there" — which reads as
    a gap someone could be told to close, and no suite can close this one).

    Why this exists rather than a format spec: ``d["closure"]`` is ``None`` for
    such a coverpoint, and ``f"{None:>6.0%}"`` raises. The command formatted it
    directly, so a single not-measurable dimension killed the whole table
    mid-print — the operator saw trajectory, tool_condition and agent_steps and
    never reached data_condition, action_risk, the instrumentation-fidelity block
    or the uninstrumented-tools list. Two renderers exist because Markdown and
    console markup are different languages, not because the rule differs;
    ``tests/test_cli_verify_traffic.py`` pins them to the same three states.
    """
    if d.get("not_measurable"):
        why = (d.get("not_measurable_reason") or "").strip()
        # escaped: a reason containing a bracketed phrase would otherwise be
        # eaten by rich's markup parser, and half a stated reason is worse than
        # a whole one — this text IS the disclosure.
        return "[yellow]not measurable[/]" + (f" — {escape(why)}" if why else "")
    closure = d.get("closure")
    if closure is None:
        # No declaration and no number either: still not a zero, and still not
        # ours to invent one.
        return "[yellow]not measured[/]"
    return f"{closure:>6.0%}"


@ingest_app.command("verify-traffic")
def ingest_verify_traffic(
    agent: str = typer.Option(..., "--agent", "-a", help="agent id to verify"),
    limit: int = typer.Option(0, "--limit",
                              help="only the most recent N traces (0 = all)"),
    config: str = "config.yaml",
):
    """Measure coverage closure and safety properties over PRODUCTION TRAFFIC.

    Nobody authors 95% of a situation space, which is why suite closure stalls
    around 20%. Real traffic exercises it continuously — the ingest was already
    importing it, it was simply never verified. Same coverage model, same
    properties, different population.

    Deterministic and free: zero model calls.
    """
    from agenttic.verification.traffic import traffic_window, verify_traffic
    cfg, reg = _ctx(config)

    traces = traffic_window(reg, agent_id=agent, limit=limit or None)
    if not traces:
        console.print(
            f"[yellow]No live traces for {agent}.[/] Ingest some first: "
            "agenttic ingest otel <spans.json>")
        raise typer.Exit(code=1)

    v = verify_traffic(traces, cfg=cfg)
    if v.get("status") != "populated":
        console.print(f"[red]Verification did not run:[/] {v.get('note')}")
        raise typer.Exit(code=1)

    a = v.get("assertions") or {}
    console.print(f"\n[bold]Closure over production traffic — {agent}[/]")
    console.print(f"  {v['scope_statement']}")
    console.print(
        f"  closure          {v['trace_closure']:.1%} of "
        f"{v['closure_target']:.0%} target   closed={v['closed']}")
    console.print(
        f"  properties       {a.get('total', 0)} checked, "
        f"{a.get('violations', 0)} VIOLATED, "
        f"{a.get('unexercised', 0)} never exercised")

    for viol in a.get("violated_properties") or []:
        console.print(f"  [red][{viol['severity'].upper()}][/] "
                      f"{viol['assertion_id']} — {viol['detail']} "
                      f"({viol['traces']})")

    console.print("\n  [bold]per coverpoint[/]")
    for cp, d in (v.get("per_coverpoint") or {}).items():
        miss = ", ".join(d.get("unhit") or [])
        console.print(f"    {cp:16} {_closure_cell(d)}"
                      + (f"   missing: {miss}" if miss else ""))
    waived = v.get("waived_bins") or {}
    if waived:
        # Hard Rule 61: a bin outside the closure denominator is named here with
        # its reason. Without it the `missing:` lists above are shorter than the
        # model's bin count for no stated cause, and a reader would take the
        # shortfall for coverage.
        console.print("\n  [bold]excluded from closure[/]")
        # Grouped by reason, not per bin: every bin of a not-measurable
        # coverpoint carries the SAME (long) reason, and printing it once per bin
        # buries the disclosure in repetition. Grouping shortens nothing that
        # matters — each bin is still named and each reason still stated in full.
        by_reason: dict[str, list[str]] = {}
        for b, why in waived.items():
            by_reason.setdefault(why, []).append(b)
        for why, bins in by_reason.items():
            console.print(f"    [dim]{escape(', '.join(bins))} — "
                          f"{escape(why)}[/]")

    f = v["instrumentation"]
    console.print("\n  [bold]instrumentation fidelity[/]")
    console.print(f"    tool spans       {f['tool_spans']} "
                  f"({f['by_confidence']})")
    console.print(f"    action_risk trustable  {f['action_risk_trustable']:.0%}")
    for w in v.get("warnings") or []:
        console.print(f"  [yellow]![/] {w}")
    if f["uninstrumented_tools"]:
        # These names come from SOMEONE ELSE'S spans, so they are the one string
        # here that is neither a repo constant nor a number. Rich silently eats
        # anything in square brackets as a style tag, so `fetch[v2]` would print
        # as `fetch` — and this list is the operator's to-do list: a name printed
        # with a piece missing is a tool they cannot find to instrument.
        console.print("    [dim]instrument these to make action_risk real: "
                      + ", ".join(f"{escape(str(n))} (×{c})"
                                  for n, c in f["uninstrumented_tools"]) + "[/]")


# --- hook: verify a coding agent from its own tool-use stream ---------------
hook_app = typer.Typer(
    help="Capture a coding agent's tool calls and verify them.")
app.add_typer(hook_app, name="hook")


@hook_app.command("claude-code")
def hook_claude_code():
    """PostToolUse hook: read one event on stdin, append a span, exit 0.

    Wire it up with `agenttic hook install`. Always exits 0 — a hook that breaks
    the agent it observes is worse than no hook.
    """
    from agenttic.hooks.claude_code import main as hook_main
    raise typer.Exit(code=hook_main())


def _report(res, what: str) -> None:
    """Say exactly what happened to which file — never 'done'."""
    if res.action == "error":
        console.print(f"[red]Could not install {what}:[/] {res.detail}")
        raise typer.Exit(code=1)
    if res.action == "already":
        console.print(f"[dim]{what}: already set up in {res.path} — nothing to do[/]")
        return
    if res.action == "skipped":
        console.print(f"[dim]{what}: {res.detail}[/]")
        return
    console.print(f"[green]{what} → {res.path}[/]"
                  + (f"\n[dim]  previous file saved as {res.backup.name}[/]"
                     if res.backup else ""))


@hook_app.command("install")
def hook_install(
    settings: str = typer.Option("", "--settings",
                                 help="settings file (default ~/.claude/settings.json)"),
    mcp: bool = typer.Option(True, "--mcp/--no-mcp",
                             help="also register the MCP server"),
    dry_run: bool = typer.Option(False, "--dry-run",
                                 help="print what would change, write nothing"),
):
    """Set it up: register the tool-use hook AND the MCP server.

    Edits your assistant's settings in place, merging rather than replacing, and
    leaves a timestamped backup beside anything it touches. Re-running is safe.
    """
    import json as _json

    from agenttic.hooks.claude_code import DEFAULT_SPOOL, SPOOL_ENV
    from agenttic.hooks.install import (
        CLAUDE_SETTINGS, HOOK_COMMAND, MCP_ENTRY, MCP_SERVER_NAME,
        detect_mcp_targets, install_hook, install_mcp)

    if dry_run:
        console.print("[bold]Would add to "
                      f"{settings or CLAUDE_SETTINGS}:[/]")
        console.print(_json.dumps({"hooks": {"PostToolUse": [
            {"matcher": "*", "hooks": [{"type": "command",
                                        "command": HOOK_COMMAND}]}]}}, indent=2))
        if mcp:
            for name, path in detect_mcp_targets() or []:
                console.print(f"\n[bold]Would add to {path} ({name}):[/]")
                console.print(_json.dumps(
                    {"mcpServers": {MCP_SERVER_NAME: MCP_ENTRY}}, indent=2))
        console.print("\n[dim]nothing was written (--dry-run)[/]")
        return

    _report(install_hook(settings or CLAUDE_SETTINGS), "tool-use hook")

    if mcp:
        targets = detect_mcp_targets()
        if not targets:
            console.print("[yellow]No MCP client config found on this machine.[/] "
                          "Add this to yours by hand:")
            console.print(_json.dumps(
                {"mcpServers": {MCP_SERVER_NAME: MCP_ENTRY}}, indent=2))
        for name, path in targets:
            _report(install_mcp(name), f"MCP server ({name})")

    console.print(f"\n[dim]what the agent does spools to {DEFAULT_SPOOL} "
                  f"(override with {SPOOL_ENV})[/]")
    console.print("[dim]then, after working as normal: agenttic hook verify[/]")


@hook_app.command("uninstall")
def hook_uninstall(
    settings: str = typer.Option("", "--settings", help="settings file"),
):
    """Remove our hook and MCP entry, leaving everything else untouched."""
    from agenttic.hooks.install import (
        CLAUDE_SETTINGS, detect_mcp_targets, uninstall_hook, uninstall_mcp)
    _report(uninstall_hook(settings or CLAUDE_SETTINGS), "tool-use hook removed")
    for name, _p in detect_mcp_targets():
        _report(uninstall_mcp(name), f"MCP server removed ({name})")


@hook_app.command("verify")
def hook_verify(
    spool: str = typer.Option("", "--spool", help="span file (default: the spool)"),
    agent: str = typer.Option("", "--agent", help="only this agent id"),
):
    """Closure + safety properties over the tool calls the hook captured.

    No registry and no config needed — this reads the spool directly, so it works
    inside any repo the moment the hook is installed. Deterministic, zero model
    calls.
    """
    from pathlib import Path as _P

    from agenttic.hooks.claude_code import as_otlp, load_spool
    from agenttic.ingest.mapping import spans_to_traces
    from agenttic.ingest.otel import parse_otlp
    from agenttic.verification.traffic import verify_traffic

    spans = load_spool(_P(spool).expanduser() if spool else None)
    if not spans:
        console.print("[yellow]No captured tool calls yet.[/] "
                      "Run `agenttic hook install` and use the agent first.")
        raise typer.Exit(code=1)

    # spans_to_traces is the registry-free seam: no config, no db, works in any repo
    traces, _decisions, _report = spans_to_traces(parse_otlp(as_otlp(spans)))
    if agent:
        traces = [t for t in traces if t.agent_id == agent]
    if not traces:
        console.print(f"[yellow]No traces for {agent!r}.[/]")
        raise typer.Exit(code=1)

    v = verify_traffic(traces)
    if v.get("status") != "populated":
        console.print(f"[red]Verification did not run:[/] {v.get('note')}")
        raise typer.Exit(code=1)

    a = v.get("assertions") or {}
    console.print(f"\n[bold]What this agent has actually been observed doing[/]")
    console.print(f"  {len(spans)} tool call(s) across {v['n_traces']} session(s)")
    console.print(f"  closure     {v['trace_closure']:.1%} of "
                  f"{v['closure_target']:.0%}   closed={v['closed']}")
    # That fraction is over the dimensions something can feed. Any dimension with
    # no producer left the denominator, and a closure figure that does not say so
    # is a better-looking number describing a smaller space — the same
    # over-report, one layer out. `ingest verify-traffic` states it; these two
    # commands read the IDENTICAL verify_traffic dict, so they say the identical
    # thing through the identical renderer rather than a second format rule that
    # could drift into two answers about one coverpoint.
    #
    # Keyed on `closure is None` rather than on the flag, because that is the
    # condition that actually matters here — out of the denominator — and
    # `_closure_cell` already distinguishes the declared case ("not measurable",
    # with the reason) from the undeclared one ("not measured"). Unlike its
    # sibling this command prints no `missing:` lists, so there is no shortened
    # bin list needing the per-bin `waived_bins` block to explain it.
    outside = [(cp, d) for cp, d in (v.get("per_coverpoint") or {}).items()
               if d.get("closure") is None]
    if outside:
        console.print("    [dim]outside that denominator:[/]")
        for cp, d in outside:
            console.print(f"      {cp:16} {_closure_cell(d)}")
    console.print(f"  properties  {a.get('total', 0)} checked, "
                  f"{a.get('violations', 0)} VIOLATED, "
                  f"{a.get('unexercised', 0)} never exercised")
    for viol in a.get("violated_properties") or []:
        console.print(f"  [red][{viol['severity'].upper()}][/] "
                      f"{viol['assertion_id']} — {viol['detail']} "
                      f"({viol['traces']})")
    for p in a.get("unexercised_properties") or []:
        console.print(f"  [dim]never exercised: {p}[/]")

    f = v["instrumentation"]
    console.print(f"\n  action_risk trustable {f['action_risk_trustable']:.0%} "
                  f"({f['by_confidence']})")
    if f["uninstrumented_tools"]:
        # escaped for the same reason as its sibling above: these are tool names
        # captured from a real agent, and a name printed with a bracketed piece
        # eaten by rich's markup parser names no tool the operator can go find.
        console.print("  [yellow]unclassifiable[/]: "
                      + ", ".join(f"{escape(str(n))} (×{c})"
                                  for n, c in f["uninstrumented_tools"]))


@app.command("mcp")
def mcp_serve():
    """Run the MCP stdio server so an agent can query its own evidence.

    Exposes `classify_command` (ask BEFORE running a shell command whether it is
    irreversible), `verify_session`, and `what_is_untested`.

    Register it with any MCP client as:
        {"command": "agenttic", "args": ["mcp"]}
    """
    from agenttic.mcp_server import main as mcp_main
    raise typer.Exit(code=mcp_main())


@hook_app.command("export")
def hook_export(
    out: str = typer.Option("hook-spans.json", "--out", help="OTLP JSON output"),
):
    """Export the spool as an OTLP dump for `agenttic ingest otel`."""
    import json as _json
    from pathlib import Path as _P

    from agenttic.hooks.claude_code import as_otlp, load_spool
    spans = load_spool()
    _P(out).write_text(_json.dumps(as_otlp(spans), indent=2), encoding="utf-8")
    console.print(f"[green]{len(spans)} span(s)[/] → {out}")
    console.print(f"[dim]agenttic ingest otel {out}[/]")


@app.command()
def evaluate(
    inputs: str = typer.Argument(
        ..., help="business doc / agent description — a file path or inline text"),
    description: str = typer.Option(
        "", "--description", "-d", help="extra agent description"),
    threshold: float = typer.Option(0.5, help="classification confidence threshold"),
    suite_id: str = typer.Option("eval-suite", help="id for the generated suite"),
    out: str = typer.Option("", "--out", help="write the review markdown here"),
    config: str = "config.yaml",
):
    """The Rubric Engine behind one command (SPEC-9 Step 44): classify the agent
    into an archetype, synthesize its rubric (mostly reused proven criteria + a
    small audited delta) and a matched suite, run the integrity gates, and present
    a draft with its evidence for approval. Discrimination (the fit gate) runs when
    a reference panel is available; without one, the draft is reported as
    awaiting-discrimination and is NOT shippable (Hard Rule 39)."""
    from pathlib import Path as _P

    from agenttic.rubric_engine.classify import ClassifyInputs
    from agenttic.rubric_engine.evaluate import AWAITING_APPROVAL
    from agenttic.rubric_engine.evaluate import evaluate as _eval

    p = _P(inputs)
    doc = p.read_text(encoding="utf-8") if p.exists() else inputs
    ci = ClassifyInputs(business_doc=doc, agent_description=description)
    result = _eval(ci, business_context=doc, threshold=threshold, suite_id=suite_id)

    console.print(f"[bold]state:[/] {result.state}")
    if result.matches:
        console.print("[bold]classification:[/]")
        for m in result.matches:
            console.print(f"  • {m.archetype_id} — {m.confidence:.2f} "
                          f"[dim]({m.source})[/]")
    if result.draft is not None:
        s = result.draft.feature_summary()
        console.print(
            f"[bold]rubric:[/] {s['n_criteria']} criteria — "
            f"[green]{s['reuse_ratio']*100:.0f}% reused[/] proven "
            f"({s['n_core']} core + {s['n_ethos']} ethos + {s['n_delta']} delta)")
        console.print(f"[bold]suite features:[/] "
                      f"{', '.join(result.draft.required_suite_features)}")
    if result.discrimination is not None:
        v = "PASS" if result.discrimination.passes_gate else "FAIL"
        console.print(f"[bold]discrimination gate:[/] {v} — "
                      f"{result.discrimination.reason}")
    for r in result.reasons:
        console.print(f"[yellow]→[/] {r}")
    if result.state == AWAITING_APPROVAL and result.fit_verified:
        console.print("[green]fit-verified — awaiting approval[/]")
    if out and result.review:
        _P(out).write_text(result.review, encoding="utf-8")
        console.print(f"[dim]review written to {out}[/]")


# ---------------------------------------------------------------------------
# SPEC-12 Step 54 — attestation: sign the evidence, never the verdict
# ---------------------------------------------------------------------------

@app.command()
def attest(
    scorecard_id: str = typer.Argument(..., help="scorecard to attest"),
    agent_config_hash: str = typer.Option(
        "", "--config-hash",
        help="the exact agent config hash the certificate binds to"),
    tier: str = typer.Option("local_self_attested", "--tier",
                             help="local_self_attested | assurance"),
    k: int = typer.Option(1, "--k", help="trials per case behind this scorecard"),
    expires_in: int = typer.Option(90, "--expires-in", help="days until expiry"),
    out: str = typer.Option("manifest.json", "--out"),
    config: str = "config.yaml",
):
    """Sign an evidence manifest for a scorecard (SPEC-12 Step 54).

    Attests WHAT WAS MEASURED, under which conditions, by whom — never that the
    agent is safe. The local tier proves integrity (nothing was altered), not
    neutrality; the artifact says so."""
    from pathlib import Path as _P

    from agenttic.certification.attest import (
        render_certificate, sign_manifest)
    from agenttic.certification.attest import build_manifest as _build
    _cfg, reg = _ctx(config)
    try:
        sc = reg.get_scorecard(scorecard_id)
    except Exception as exc:
        raise typer.BadParameter(f"unknown scorecard {scorecard_id}: {exc}")

    from agenttic.certification.attest import SignoffRefused
    from agenttic.ops import signoff_from_run

    signoff = signoff_from_run(sc)
    if signoff is None:
        console.print(
            "[red]This scorecard carries no verification sign-off, so there is "
            "nothing to stand behind.[/]\n"
            "Scorecards recorded before sign-offs were persisted cannot be "
            "certified — re-run the suite and attest the new scorecard.")
        raise typer.Exit(code=2)

    sc_dict = sc.model_dump(mode="json")
    manifest = _build(
        manifest_id=f"manifest-{scorecard_id}",
        agent_id=sc.agent_id,
        agent_config_hash=agent_config_hash or f"unpinned:{sc.agent_id}",
        suite_id=sc.suite_id, suite_version=sc.suite_version,
        rubric_id=sc.rubric_id, rubric_version=sc.rubric_version,
        scorecard=sc_dict, visibility_tier=sc.visibility_tier, k=k,
        expires_in_days=expires_in, signing_tier=tier,
        issuer=("agenttic-assurance" if tier == "assurance" else "local-self-attested"),
        signoff=signoff,
    )
    try:
        signed = sign_manifest(manifest, signoff=signoff, cfg=_cfg)
    except SignoffRefused as exc:
        # The whole point: no certificate is produced. Report why, in the terms
        # the reader has to act on, and exit non-zero so CI fails too.
        console.print(f"[red]REFUSED — no certificate issued.[/]\n{exc}")
        summary = signoff.scope_summary()
        console.print(
            f"\n[dim]scope: {summary.exercised_label} · closure "
            f"{summary.trace_closure:.1%} of {summary.closure_target:.0%} · "
            f"{summary.violations} violation(s)[/]")
        if summary.unexercised_properties:
            console.print("[dim]never exercised: "
                          + ", ".join(summary.unexercised_properties) + "[/]")
        raise typer.Exit(code=3)
    _P(out).write_text(signed.model_dump_json(indent=2), encoding="utf-8")
    console.print(render_certificate(signed))
    console.print(f"\n[green]Signed manifest written to {out}[/]")
    if not agent_config_hash:
        console.print("[yellow]→ no --config-hash given: the certificate is not "
                      "pinned to an exact agent version (Hard Rule 53).[/]")


@app.command()
def verify(
    manifest_path: str = typer.Argument(..., help="signed manifest JSON"),
    config_hash: str = typer.Option("", "--config-hash",
                                    help="the DEPLOYED agent's config hash"),
    revocations: str = typer.Option("", "--revocations",
                                    help="signed revocation list JSON"),
    config: str = "config.yaml",
):
    """Verify a signed manifest: signature, recomputed evidence hashes, subject
    binding, expiry and revocation — reporting a precise reason on failure."""
    import json as _json
    from pathlib import Path as _P

    from agenttic.certification.attest import verify_manifest
    from agenttic.schema.attestation import RevocationList, SignedManifest
    _cfg, reg = _ctx(config)

    signed = SignedManifest.model_validate_json(
        _P(manifest_path).read_text(encoding="utf-8"))
    # recompute the scorecard hash from the STORED scorecard where we can
    scorecard = None
    try:
        sid = signed.manifest.manifest_id.replace("manifest-", "", 1)
        scorecard = reg.get_scorecard(sid).model_dump(mode="json")
    except Exception:
        console.print("[dim]stored scorecard not found — verifying signature and "
                      "manifest integrity only[/]")
    rl = None
    if revocations:
        raw = _json.loads(_P(revocations).read_text(encoding="utf-8"))
        rl = RevocationList.model_validate(raw.get("revocation_list", raw))

    res = verify_manifest(signed, scorecard=scorecard, revocations=rl,
                          current_config_hash=config_hash or None)
    colour = {"valid": "green", "expired": "yellow",
              "suspended": "yellow", "revoked": "red"}.get(res.status, "red")
    console.print(f"[{colour}]{res.status.upper()}[/] — {res.reason}")
    for p in res.problems:
        console.print(f"  [red]•[/] {p}")
    if not res.ok:
        raise typer.Exit(1)


@app.command()
def abom(
    agent: str = typer.Argument(..., help="agent id (the BOM subject)"),
    model: list[str] = typer.Option([], "--model", help="model id (repeatable)"),
    tool: list[str] = typer.Option([], "--tool", help="tool name[@version] (repeatable)"),
    mcp: list[str] = typer.Option([], "--mcp", help="MCP server name[@version] (repeatable)"),
    out: str = typer.Option("abom.json", "--out"),
    config: str = "config.yaml",
):
    """Emit the Agent BOM (CycloneDX) — the supply chain behind the agent:
    models, prompt hashes, tools, MCP servers, suite/rubric, harness, deps."""
    from pathlib import Path as _P

    from agenttic import __version__ as _hv
    from agenttic.certification.abom import (
        abom_json, abom_sha256, build_abom, validate_abom)

    def _split(items):
        out_ = []
        for raw in items:
            name, _, ver = raw.partition("@")
            out_.append({"name": name, "version": ver})
        return out_

    doc = build_abom(
        subject_name=agent, model_ids=list(model),
        tools=_split(tool), mcp_servers=_split(mcp),
        harness_version=str(_hv))
    validate_abom(doc)
    _P(out).write_text(abom_json(doc), encoding="utf-8")
    console.print(f"[green]ABOM written to {out}[/] "
                  f"({len(doc['components'])} components)")
    console.print(f"sha256 {abom_sha256(doc)}")
    console.print("[dim]reference this hash from the manifest (--abom-sha256)[/]")


@app.command("certify-mcp")
def certify_mcp(
    target: str = typer.Argument(
        ..., help="stdio command (quoted) or an http(s):// endpoint"),
    write_tool: str = typer.Option("", "--write-tool",
                                   help="a mutating tool to probe for idempotency"),
    gated_tool: str = typer.Option("", "--gated-tool",
                                   help="a permission-gated tool to probe for authz"),
    mutating: list[str] = typer.Option(
        [], "--mutating",
        help="tool that DOES mutate (operator ground truth, repeatable). Without "
             "it the side-effect-disclosure check is skipped, never assumed."),
    goldens: str = typer.Option("", "--goldens", help="pinned goldens JSON"),
    record: str = typer.Option("", "--record-goldens",
                               help="write goldens for THIS server version here"),
    out: str = typer.Option("", "--out", help="write the server scorecard JSON here"),
    attest_out: str = typer.Option("", "--attest",
                                   help="also write a signed manifest here"),
    config: str = "config.yaml",
):
    """Certify an MCP SERVER as the device under test (SPEC-12 Step 55).

    Runs the battery — contract, golden responses, fuzzing, authorization, error
    taxonomy, idempotency, rate limiting, side-effect disclosure, and the
    tool-response injection probe — over stdio or HTTP."""
    import json as _json
    import shlex
    from pathlib import Path as _P

    from agenttic.adapters.mcp_server import connect_http, connect_stdio
    from agenttic.certification.mcp_suite import (
        certify_mcp_server, manifest_for_server, record_goldens, valid_args)

    mk = (lambda: connect_http(target)) if target.startswith("http") \
        else (lambda: connect_stdio(shlex.split(target)))

    probes = [(write_tool or "", {})] if False else []
    if record:
        with mk() as c:
            tools = c.list_tools()
            probes = [(t.name, {}) for t in tools[:1]]
            g = record_goldens(c, probes)
        _P(record).write_text(_json.dumps(g, indent=2), encoding="utf-8")
        console.print(f"[green]Goldens pinned[/] for v{g['server_version']} → {record}")
        return

    pinned = _json.loads(_P(goldens).read_text()) if goldens else None
    with mk() as c:
        tools = c.list_tools()
        by_name = {t.name: t for t in tools}
        # valid arguments for the write probe come from the tool's declared
        # schema — otherwise idempotency fails on validation, not on duplication.
        w_args = valid_args(by_name[write_tool]) if write_tool in by_name else {}
        report = certify_mcp_server(
            c, goldens=pinned,
            golden_probes=[(tools[0].name, {})] if (pinned and tools) else [],
            gated_calls=[(gated_tool, valid_args(by_name[gated_tool]))]
                        if gated_tool in by_name else [],
            write_tool=write_tool or None, write_args=w_args,
            # NB: operator-declared ground truth. Deriving this from the server's
            # own declarations would be circular — a server hiding its side
            # effects would pass by definition.
            known_mutating=list(mutating))

    console.print(f"[bold]{report.server_name}[/] v{report.server_version} "
                  f"({report.transport}) — {len(report.tools)} tool(s)")
    for o in report.outcomes:
        mark = ("[dim]skip[/]" if o.skipped
                else "[green]pass[/]" if o.passed else "[red]FAIL[/]")
        console.print(f"  {mark}  {o.check_id}: {o.detail}")
    console.print(f"[bold]score {report.score:.2f}[/] — "
                  + ("[green]certified[/]" if report.passed
                     else f"[red]failed: {', '.join(report.failed)}[/]"))
    if out:
        _P(out).write_text(_json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]scorecard → {out}[/]")
    if attest_out:
        from agenttic.certification.attest import SignoffRefused, sign_manifest
        from agenttic.certification.mcp_suite import signoff_for_server
        cfg, _reg = _ctx(config)
        signoff = signoff_for_server(report)
        try:
            signed = sign_manifest(
                manifest_for_server(report, manifest_id=f"mcp-{report.server_name}"),
                signoff=signoff, cfg=cfg)
        except SignoffRefused as exc:
            console.print(f"[red]REFUSED — no certificate issued.[/]\n{exc}")
            raise typer.Exit(code=3)
        _P(attest_out).write_text(signed.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[dim]signed manifest → {attest_out}[/]")
    if not report.passed:
        raise typer.Exit(1)


def _load_object(path: str):
    """Import ``module:attr`` and return it. The adapter seam: a memory backend
    is whatever the operator points at, not something we ship a driver for."""
    import importlib
    if ":" not in path:
        raise typer.BadParameter(
            f"expected 'module:attribute', got {path!r} "
            "(e.g. 'myapp.memory:build_store')")
    mod_name, attr = path.split(":", 1)
    return getattr(importlib.import_module(mod_name), attr)


@app.command("certify-memory")
def certify_memory_cmd(
    store: str = typer.Option(
        "", "--store",
        help="'module:attribute' resolving to a MemoryStore, or a zero-arg "
             "factory returning one"),
    reference: bool = typer.Option(
        False, "--reference",
        help="run the battery against the built-in reference store (a smoke "
             "test of the battery itself, not of your memory)"),
    name: str = typer.Option("", "--name", help="store name recorded in evidence"),
    version: str = typer.Option("", "--version", help="store version"),
    capacity: int = typer.Option(
        0, "--capacity",
        help="capacity you DECLARE the store has. Without it the capacity check "
             "is skipped, never assumed — asking the store would certify its "
             "own answer."),
    out: str = typer.Option("", "--out", help="write the memory scorecard JSON here"),
    attest_out: str = typer.Option("", "--attest",
                                   help="also write a signed manifest here"),
    config: str = "config.yaml",
):
    """Certify a MEMORY store as the device under test (SPEC-12 Step 57).

    Memory is the part of the supply chain an agent-level evaluation cannot
    reach: every defect here — a fact leaking between principals, a deletion
    honoured in one index only, a stale value beating a newer one, an
    instruction smuggled in as a stored fact — is invisible inside one session
    and obvious across two."""
    import json as _json
    from pathlib import Path as _P

    from agenttic.certification.memory_suite import certify_memory, manifest_for_memory

    if reference:
        from agenttic.camp.memory import ReferenceMemoryStore
        obj, label = ReferenceMemoryStore(capacity=capacity or 64), name or "reference"
        capacity = capacity or 64
    elif store:
        obj = _load_object(store)
        # accept a class, a zero-arg factory, or an already-built store
        if isinstance(obj, type) or (callable(obj) and not hasattr(obj, "write")):
            obj = obj()
        label = name or store.rsplit(":", 1)[-1]
    else:
        raise typer.BadParameter("pass --store module:attribute, or --reference")

    report = certify_memory(obj, store_name=label, store_version=version,
                            declared_capacity=capacity or None)

    console.print(f"[bold]{report.store_name}[/]"
                  + (f" v{report.store_version}" if report.store_version else "")
                  + " — memory certification")
    for o in report.outcomes:
        mark = ("[dim]skip[/]" if o.skipped
                else "[green]pass[/]" if o.passed else "[red]FAIL[/]")
        console.print(f"  {mark}  {o.check_id}: {o.detail}")
    console.print(f"[bold]score {report.score:.2f}[/] — "
                  + ("[green]certified[/]" if report.passed
                     else f"[red]failed: {', '.join(report.failed)}[/]"))
    if report.critical_failures:
        console.print("[red]critical:[/] " + ", ".join(report.critical_failures)
                      + " — these have a blast radius outside the agent")
    if out:
        _P(out).write_text(_json.dumps(report.as_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]scorecard → {out}[/]")
    if attest_out:
        from agenttic.certification.attest import SignoffRefused, sign_manifest
        from agenttic.certification.memory_suite import signoff_for_memory
        cfg, _reg = _ctx(config)
        signoff = signoff_for_memory(report)
        try:
            signed = sign_manifest(
                manifest_for_memory(report, manifest_id=f"memory-{report.store_name}"),
                signoff=signoff, cfg=cfg)
        except SignoffRefused as exc:
            console.print(f"[red]REFUSED — no certificate issued.[/]\n{exc}")
            raise typer.Exit(code=3)
        _P(attest_out).write_text(signed.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"[dim]signed manifest → {attest_out}[/]")
    if not report.passed:
        raise typer.Exit(1)


@app.command("catalog-check")
def catalog_check_cmd(
    catalog_file: str = typer.Argument(..., help="an exported catalog JSON"),
    manifests: str = typer.Option(
        "", "--manifests",
        help="directory of signed manifest JSON files to verify entries against"),
    warn_ok: bool = typer.Option(
        False, "--warn-ok",
        help="exit 0 when only warnings were found (errors always exit 1)"),
):
    """Check a conformance catalog: is everything it approves still supported?
    (SPEC-12 Step 58)

    Reports lapsed or revoked evidence, entries disturbed by a retired
    dependency, and agents promoted on the strength of a component that is not
    itself promoted. It reports; it never repairs — silently downgrading an entry
    would hide the window in which something was approved on lapsed evidence."""
    import json as _json
    from pathlib import Path as _P

    from agenttic.certification.catalog import Catalog
    from agenttic.schema.attestation import SignedManifest

    cat = Catalog.from_export(_json.loads(_P(catalog_file).read_text(encoding="utf-8")))
    supplied: dict = {}
    if manifests:
        for f in sorted(_P(manifests).glob("*.json")):
            try:
                sm = SignedManifest.model_validate_json(f.read_text(encoding="utf-8"))
            except Exception:                       # not a manifest — skip quietly
                continue
            supplied[sm.manifest.manifest_id] = sm

    findings = cat.check_conformance(manifests=supplied or None)
    counts = cat.export()["counts"]
    console.print(f"[bold]{cat.owner}[/] catalog — "
                  + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    if not findings:
        console.print("[green]conformant[/] — every promoted entry has live "
                      "evidence and promoted dependencies")
        return
    for f in findings:
        tag = "[red]ERROR[/]" if f.severity == "error" else "[yellow]warn[/]"
        console.print(f"  {tag}  {f.entry_ref}: {f.problem} — {f.detail}")
    errors = [f for f in findings if f.severity == "error"]
    console.print(f"[bold]{len(errors)} error(s), "
                  f"{len(findings) - len(errors)} warning(s)[/]")
    if errors or not warn_ok:
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
