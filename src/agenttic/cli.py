"""agenttic CLI — the operator surface (SPEC.md `CLI surface`).

Exposed as the ``agenttic`` console script (and ``python -m agenttic``). The
legacy ``ascore`` command remains as a deprecated alias that forwards here.

Requires ANTHROPIC_API_KEY in the environment for commands that call models
(generate, run with judge criteria, monitor with sampling).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agenttic import ops
from agenttic.config import load_config
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.scorecard import Scorecard
from agenttic.scoring.calibration import calibration_report, load_labels

app = typer.Typer(help="Agentic scoring & benchmarking platform")
console = Console()


def _ascore_alias() -> None:
    """Deprecated ``ascore`` console-script entry point.

    ``ascore`` is the pre-rename command name. It still works — it forwards to
    the identical ``agenttic`` CLI — but prints a one-line deprecation nudge to
    stderr so operators migrate. Same behavior, same exit codes."""
    import sys
    print("warning: the `ascore` command is deprecated; use `agenttic` instead "
          "(identical behavior).", file=sys.stderr)
    app()

# Global --tenant (or ASCORE_TENANT) selects the workspace for every command.
_STATE: dict[str, str | None] = {"tenant": None}


@app.callback()
def _main(tenant: str = typer.Option(
        None, "--tenant", envvar=["AGENTTIC_TENANT", "ASCORE_TENANT"],
        help="workspace/tenant to operate on (default: 'default')")):
    """Agenttic CLI. The CLI operates directly on the registry DB (admin-level);
    --tenant selects the workspace, matching the server's tenancy model."""
    _STATE["tenant"] = tenant


def _ctx(config_path: str = "config.yaml"):
    from agenttic.secrets import hydrate_env_secrets
    hydrate_env_secrets()  # pull *_FILE secrets into the environment
    cfg = load_config(config_path)
    from agenttic._env import get_env
    tenant = _STATE.get("tenant") or get_env("ASCORE_TENANT") or "default"
    db_url = get_env("ASCORE_DB") or (cfg.get("database", {}) or {}).get("url") or ""
    if db_url and not db_url.startswith("sqlite"):
        from agenttic.registry.sqlite_store import make_engine
        return cfg, Registry(engine=make_engine(db_url), tenant=tenant)
    # SQLite: file-per-tenant (mirrors server Workspaces)
    base = Path(cfg["paths"]["registry_db"])
    path = base if tenant == "default" \
        else base.with_name(f"{base.stem}.{tenant}{base.suffix}")
    return cfg, Registry(str(path))


@app.command()
def generate(business_doc: Path, suite_id: str, config: str = "config.yaml"):
    """Draft a test suite from a business document (requires human approval)."""
    cfg, reg = _ctx(config)
    suite = ops.generate_op(cfg, reg, business_doc.read_text(), suite_id)
    console.print(f"[yellow]DRAFT[/] suite {suite.suite_id} v{suite.version} "
                  f"({len(suite.test_ids)} cases). Review "
                  f"{cfg['paths']['review_dir']}/{suite_id}.md then run "
                  f"`uv run ascore approve {suite_id}`.")


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
        system_prompt: str = "", model: str = "", config: str = "config.yaml"):
    """Run a suite against an agent.

    If --agent matches a name in the declared catalog (`ascore agents add`), its
    connection details are used automatically — so `ascore run --agent prod
    --suite s` just works. Otherwise build one ad-hoc: the reference agent
    (--system-prompt/--model), --url for black-box HTTP, or
    --managed-agent-id/--environment-id for a deployed Managed Agent. Explicit
    flags always override the catalog."""
    from agenttic.registry.sqlite_store import NotFoundError

    cfg, reg = _ctx(config)
    variant = "managed" if managed_agent_id else ("blackbox" if url else "reference")
    bb = {}  # black-box cost hints from the declared agent
    # resolve a declared catalog agent when no connection flags were given
    if not (url or managed_agent_id):
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
    try:
        adapter = ops.build_adapter(cfg, variant=variant, agent_id=agent, url=url,
                                    managed_agent_id=managed_agent_id,
                                    environment_id=environment_id,
                                    system_prompt=system_prompt, model=model, **bb)
    except ValueError as exc:
        raise typer.BadParameter(str(exc))
    from agenttic.budget import BudgetExceededError
    try:
        sc = asyncio.run(ops.run_and_score_op(cfg, reg, adapter, suite))
    except BudgetExceededError as exc:
        console.print(f"[red]Budget cap:[/] {exc}")
        raise typer.Exit(2)
    console.print(f"Scorecard [bold]{sc.scorecard_id}[/]: success "
                  f"{sc.task_success_rate:.0%}, mean exec cost "
                  f"${sc.mean_cost_usd:.4f}, total run cost "
                  f"${sc.total_cost_usd + sc.total_scoring_cost_usd:.4f}")


@app.command()
def deploy(workflow: Path, env_name: str = "ascore-workflows",
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
        f"Run a suite against it:\n  uv run ascore run --agent {result['name']} "
        f"--suite <suite_id> --managed-agent-id {result['agent_id']} "
        f"--environment-id {result['environment_id']}")


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
                      f"`uv run ascore approve {suite.suite_id}`.")


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
                "your Anthropic credits. Set ASCORE_API_TOKEN (or auth.token) "
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


@agents_app.command("list")
def agents_list(all_: bool = typer.Option(False, "--all",
                                          help="include retired agents"),
                config: str = "config.yaml"):
    """List declared agents."""
    _, reg = _ctx(config)
    rows = reg.list_declared_agents(include_retired=all_)
    if not rows:
        console.print("No declared agents. Add one with `uv run ascore agents add`.")
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
        console.print("No users. Create one with `uv run ascore users create`.")
        return
    table = Table("email", "role", "tenant", "created")
    for u in rows:
        table.add_row(u.email, u.role, u.tenant_id, u.created_at.strftime("%Y-%m-%d"))
    console.print(table)


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
        raise typer.BadParameter(f"no card for {agent} (run `ascore cards autofill`)")
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
    import sys as _sys

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


if __name__ == "__main__":
    app()
