"""ascore CLI — the operator surface (SPEC.md `CLI surface`).

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

from ascore import ops
from ascore.config import load_config
from ascore.registry.sqlite_store import Registry
from ascore.schema.scorecard import Scorecard
from ascore.scoring.calibration import calibration_report, load_labels

app = typer.Typer(help="Agentic scoring & benchmarking platform")
console = Console()

# Global --tenant (or ASCORE_TENANT) selects the workspace for every command.
_STATE: dict[str, str | None] = {"tenant": None}


@app.callback()
def _main(tenant: str = typer.Option(
        None, "--tenant", envvar="ASCORE_TENANT",
        help="workspace/tenant to operate on (default: 'default')")):
    """Agenttic CLI. The CLI operates directly on the registry DB (admin-level);
    --tenant selects the workspace, matching the server's tenancy model."""
    _STATE["tenant"] = tenant


def _ctx(config_path: str = "config.yaml"):
    from ascore.secrets import hydrate_env_secrets
    hydrate_env_secrets()  # pull *_FILE secrets into the environment
    cfg = load_config(config_path)
    tenant = _STATE.get("tenant") or os.environ.get("ASCORE_TENANT") or "default"
    db_url = os.environ.get("ASCORE_DB") or (cfg.get("database", {}) or {}).get("url") or ""
    if db_url and not db_url.startswith("sqlite"):
        from ascore.registry.sqlite_store import make_engine
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
    from ascore.registry.sqlite_store import NotFoundError

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
    from ascore.budget import BudgetExceededError
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


def _ab_variant(reg, label: str, agent: str, model: str, prompt: str):
    """Build an ABVariant from a base agent id, resolving a declared catalog
    entry when present; --model/--prompt override it (the model/prompt A/B
    cases)."""
    from ascore.registry.sqlite_store import NotFoundError
    from ascore.schema.ab import ABVariant
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
    from ascore.ab import run_ab_op
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

    from ascore.registry.sqlite_store import DuplicateVersionError
    from ascore.schema.rubric import Rubric
    from ascore.schema.testcase import TestCase, TestSuite

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
    from ascore.migrations import migration_status, run_migrations

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
    from ascore.server.app import UI_DIST, create_app

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
        from ascore.server.auth import configured_token

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

    from ascore.schema.agent import DeclaredAgent
    from ascore.security import UnsafeURLError, validate_blackbox_url

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
    from ascore.registry.sqlite_store import NotFoundError

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
    from ascore.registry.sqlite_store import NotFoundError

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
    from ascore.server.users import DuplicateUserError, UserStore

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
    from ascore.server.users import UserStore

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

    from ascore.registry.sqlite_store import UserRow
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
    from ascore.metrics.standard_suites import seed_standard_suites
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
    from ascore.metrics.datasets import get_adapter
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
    from ascore.metrics.catalog import METRICS
    table = Table("metric", "category", "weight", "methodology")
    for m in METRICS:
        w = f"{m.weight:.3f}" + ("" if m.status == "implemented" else " (deferred)")
        table.add_row(m.name, m.category, w, m.methodology[:70] + "…")
    console.print(table)


if __name__ == "__main__":
    app()
