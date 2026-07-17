"""Learning optimizer (SPEC-2 Step 14) — the springboard.

The capstone of the learning loop. It turns the data Steps 11–13 produce into a
*better agent config*, using the Step-10 optimizer machinery as its engine:

  1. ``collect_failures`` builds a failure DOSSIER — the judge's worst criteria +
     failing-trace excerpts (via :func:`agenttic.optimizer.reflect_on_failures`)
     FOLDED with the human corrections/ratings from Step 11 (HumanFeedback).
  2. ``propose`` asks an LLM (the tenant's BYO proposer, injectable for tests) for
     N candidate system prompts (:meth:`PromptOptimizer.propose`), tagging each
     with its own ``agent_config_hash`` + a human-readable changelog entry.
  3. ``evaluate`` runs each candidate through the SAME run→score→aggregate chain
     (``ops.run_and_score_op``): a cheap subset SCREEN first, the full suite only
     for survivors — so a clearly-worse candidate never costs a full suite.
  4. ``gate`` reuses :func:`agenttic.ab.compare_scorecards` +
     :func:`agenttic.optimizer.evaluate_candidate` for the regression veto, then
     adds an ε-tolerance per-criterion floor and cost/latency budget multipliers.
  5. ``promote`` writes the survivor to the append-only agent-config LEDGER
     (Hard Rule 10) with parent-hash lineage. If the suite's domain is
     high-severity it lands ``pending_approval`` — blocked until
     ``agenttic learn approve <hash>``.

Rejected candidates are recorded too (``status="rejected"`` + reason): the ledger
is an auditable search history, not just a winners' list.

``export_preferences`` emits (trace, score) preference pairs grouped by test case
as JSONL — the raw material for offline preference tuning (we BUILD the export,
not the training).

Nothing here talks to the network directly: the proposer and agent client are
injected, exactly like :mod:`agenttic.optimizer` and the scan tests.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, Field

from agenttic import ops
from agenttic.ab import compare_scorecards
from agenttic.optimizer import (
    PromptOptimizer,
    evaluate_candidate,
    reflect_on_failures,
)
from agenttic.registry.sqlite_store import NotFoundError, Registry
from agenttic.schema.ab import ABVariant
from agenttic.schema.optimization import (
    OptimizationRun,
    PromptVersion,
)
from agenttic.schema.scorecard import Scorecard

ProgressFn = Callable[[str, dict], None]

# Defaults for the learning: config block (Hard Rule 7 — real values live in
# config.yaml; these are the safety net when a key is absent).
_DEFAULT_EPSILON = 0.02
_DEFAULT_MAX_COST_MULT = 2.0
_DEFAULT_MAX_LATENCY_MULT = 2.0
_SCREEN_FRACTION = 0.5        # subset screen size (see evaluate's docstring)
_MIN_SCREEN_CASES = 2


# -- the config ledger entry -------------------------------------------------

class AgentConfig(BaseModel):
    """One node in the agent-config promotion ledger (persisted as
    :class:`agenttic.registry.sqlite_store.AgentConfigRow`).

    A candidate the learning optimizer produced, chained to its parent by
    ``parent_hash``. ``status`` is ``promoted`` (live), ``rejected`` (kept for the
    auditable search history, with ``reason``), or ``pending_approval`` (a
    high-severity promotion awaiting ``learn approve``). ``payload`` carries the
    full config + changelog so a promotion is self-describing."""
    agent_id: str
    agent_config_hash: str
    parent_hash: str = ""
    diff_summary: str = ""
    scorecard_ids: list[str] = Field(default_factory=list)
    status: str = "promoted"          # promoted | rejected | pending_approval
    reason: str = ""
    approved_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict = Field(default_factory=dict)


def config_hash_for(agent_id: str, system_prompt: str, model: str = "") -> str:
    """Deterministic config hash for a candidate — sha256(describe())[:16],
    mirroring ``AgentAdapter.config_hash`` so a learning candidate's hash matches
    the hash the reference adapter would compute for the same (prompt, model)."""
    payload = json.dumps(
        {"agent_id": agent_id, "system_prompt": system_prompt, "model": model},
        sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# -- 1. collect_failures (the dossier) ---------------------------------------

def collect_failures(scorecard: Scorecard, traces, feedback) -> dict:
    """Build the failure dossier the optimizer edits against.

    Wraps :func:`agenttic.optimizer.reflect_on_failures` (worst criteria +
    failing cases + judge rationales) and FOLDS IN the Step-11 human signal:

    * ``correction`` feedback → ground-truth outputs the agent should have given;
    * negative ``rating`` feedback (< 1.0) → criteria a human marked wrong, merged
      into the per-criterion picture so a human veto weighs alongside the judge;
    * failing-trace excerpts (``final_output``) keyed by test case, so the
      proposer sees *what* the agent actually produced, not just that it failed.

    ``traces`` may be any iterable of Trace-like objects (need ``test_case_id`` +
    ``final_output``); ``feedback`` a list of :class:`HumanFeedback`. Both may be
    empty — the dossier degrades to the pure judge-rationale gradient."""
    rubric = _rubric_of(scorecard, traces)
    cases = _cases_of(scorecard, traces)
    reflection = reflect_on_failures(scorecard, rubric, cases) if rubric else {
        "failing_criteria": [], "per_criterion": [], "failing_cases": [],
        "n_failing": 0}

    # failing-trace excerpts keyed by test case
    failing_ids = {fc["test_id"] for fc in reflection.get("failing_cases", [])}
    excerpts: list[dict] = []
    for tr in traces or []:
        tid = getattr(tr, "test_case_id", None)
        if tid and tid in failing_ids:
            out = (getattr(tr, "final_output", "") or "")[:400]
            excerpts.append({"test_id": tid, "final_output": out})

    # human corrections + negative ratings (Step 11)
    corrections: list[dict] = []
    human_criteria: dict[str, dict] = {}
    for fb in feedback or []:
        if getattr(fb, "kind", "") == "correction" and fb.corrected_output:
            corrections.append({
                "trace_id": fb.trace_id, "source": fb.source,
                "corrected_output": (fb.corrected_output or "")[:400],
                "rationale": (fb.rationale or "")[:240]})
        rating = getattr(fb, "rating", None)
        if rating is not None and rating < 1.0 and fb.criterion_id:
            b = human_criteria.setdefault(
                fb.criterion_id, {"criterion_id": fb.criterion_id,
                                  "n_flagged": 0, "rationales": []})
            b["n_flagged"] += 1
            if fb.rationale and len(b["rationales"]) < 4:
                b["rationales"].append(
                    f"[{fb.source}] {(fb.rationale or '')[:200]}")

    return {
        **reflection,
        "trace_excerpts": excerpts,
        "human_corrections": corrections,
        "human_flagged_criteria": sorted(human_criteria.values(),
                                         key=lambda d: -d["n_flagged"]),
        "n_human_corrections": len(corrections),
    }


def _augment_reflection_prompt(dossier: dict) -> dict:
    """Fold the human signal into the shape ``build_optimizer_prompt`` consumes so
    the proposer sees corrections/flags as extra gradient. Non-destructive: it
    appends synthetic rationales onto the existing per-criterion buckets and adds
    a corrections summary criterion when there is no judge detail."""
    refl = {k: dossier[k] for k in
            ("failing_criteria", "per_criterion", "failing_cases", "n_failing")
            if k in dossier}
    per = [dict(c) for c in refl.get("per_criterion", [])]
    by_id = {c["criterion_id"]: c for c in per}
    for hc in dossier.get("human_flagged_criteria", []):
        tgt = by_id.get(hc["criterion_id"])
        note = f"HUMAN flagged this {hc['n_flagged']}x: " + \
            "; ".join(hc["rationales"][:2])
        if tgt is not None:
            tgt.setdefault("rationales", []).append(note)
        else:
            nc = {"criterion_id": hc["criterion_id"], "description":
                  "(human-flagged criterion)", "n_failed": hc["n_flagged"],
                  "rationales": [note]}
            per.append(nc)
            by_id[hc["criterion_id"]] = nc
    for corr in dossier.get("human_corrections", [])[:4]:
        if per:
            per[0].setdefault("rationales", []).append(
                f"HUMAN correction — expected: {corr['corrected_output'][:160]}")
    refl["per_criterion"] = per
    return refl


# -- 2. propose --------------------------------------------------------------

def propose(dossier: dict, current_config: dict, n: int,
            proposer=None) -> list[dict]:
    """Ask the proposer for ``n`` candidate configs targeting the dossier.

    Delegates to :meth:`PromptOptimizer.propose` (the OPRO/ProTeGi call, injected
    for tests). Each returned candidate is enriched with:

    * ``agent_config_hash`` — its own deterministic hash (so it's ledger-ready);
    * ``changelog`` — a one-line human-readable summary of the edit;
    * ``system_prompt`` / ``model`` / ``rationale`` carried through.

    ``current_config`` supplies the baseline ``system_prompt``/``model``/``agent_id``
    the edits derive from."""
    assert proposer is not None, "propose requires an injected proposer"
    agent_id = current_config.get("agent_id", "")
    model = current_config.get("model", "")
    base_prompt = current_config.get("system_prompt", "")
    reflection = _augment_reflection_prompt(dossier)
    raw = proposer.propose(base_prompt, reflection, n) or []

    out: list[dict] = []
    for i, prop in enumerate(raw):
        prompt = (prop.get("prompt") or "").strip()
        if not prompt:
            continue
        rationale = str(prop.get("rationale", ""))[:300]
        out.append({
            "index": i,
            "system_prompt": prompt,
            "model": model,
            "rationale": rationale,
            "agent_config_hash": config_hash_for(agent_id, prompt, model),
            "changelog": (rationale or "system-prompt edit")[:200],
        })
    return out


# -- 3. evaluate -------------------------------------------------------------

def _subset_suite_id(reg: Registry, suite_id: str, version: Optional[int],
                     candidate_hash: str, cfg: dict) -> Optional[str]:
    """Materialize a cheap SCREEN sub-suite (first ``_SCREEN_FRACTION`` of the
    cases, min ``_MIN_SCREEN_CASES``) as its own approved suite so the standard
    run/score plumbing can execute it. Returns None when the suite is already
    small enough to just run whole."""
    from agenttic.schema.testcase import TestSuite
    suite, cases = reg.get_suite(suite_id, version)
    ids = sorted(c.test_id for c in cases)
    n_screen = max(_MIN_SCREEN_CASES, int(round(len(ids) * _SCREEN_FRACTION)))
    if n_screen >= len(ids):
        return None                     # nothing gained; caller runs full suite
    keep = set(ids[:n_screen])
    sub = [c.model_copy(update={"suite_id": f"learn-screen--{candidate_hash}",
                                "version": 1})
           for c in cases if c.test_id in keep]
    sid = f"learn-screen--{candidate_hash}"
    screen = TestSuite(
        suite_id=sid, version=1,
        business_context=json.dumps({"kind": "learning_screen",
                                     "source_suite_id": suite_id}),
        test_ids=[c.test_id for c in sub], approved=True)
    try:
        reg.save_suite(screen, sub)
    except Exception:
        pass                            # already materialized (re-run) — reuse it
    return sid


async def evaluate(candidates: list[dict], reg: Registry, cfg: dict,
                   suite_id: str, *, agent_id: str, version: int | None = None,
                   baseline_sc: Scorecard | None = None,
                   client=None, judge_client=None,
                   on_progress: ProgressFn | None = None,
                   ) -> list[tuple[dict, Scorecard]]:
    """Run each candidate through ``ops.run_and_score_op`` and return
    ``[(candidate, full_suite_scorecard), ...]``.

    Two-phase to keep spend down (documented heuristic): each candidate is first
    SCREENED on a subset (the first ``_SCREEN_FRACTION`` of the suite's cases, at
    least ``_MIN_SCREEN_CASES``). A candidate whose screen pass-rate is strictly
    below the baseline's screen pass-rate (when a ``baseline_sc`` is given) is
    dropped WITHOUT a full-suite run — a clearly-worse candidate never costs the
    full suite. Survivors are then scored on the full suite (the scorecard the
    gate consumes). When the suite is too small to screen meaningfully, the screen
    is skipped and the full suite is run directly."""
    out: list[tuple[dict, Scorecard]] = []
    base_screen_rate = None
    baseline_prompt = candidates[0].get("baseline_prompt", "") if candidates else ""
    for cand in candidates:
        chash = cand["agent_config_hash"]
        screen_sid = _subset_suite_id(reg, suite_id, version, chash, cfg)
        if screen_sid is not None:
            if base_screen_rate is None and baseline_sc is not None:
                base_sc_screen = await _run_candidate(
                    cfg, reg, agent_id, baseline_prompt, screen_sid,
                    model=cand.get("model", ""), client=client,
                    judge_client=judge_client, on_progress=on_progress)
                base_screen_rate = base_sc_screen.task_success_rate
            screen_sc = await _run_candidate(
                cfg, reg, agent_id, cand["system_prompt"], screen_sid,
                model=cand.get("model", ""), client=client,
                judge_client=judge_client, on_progress=on_progress)
            if base_screen_rate is not None and \
                    screen_sc.task_success_rate < base_screen_rate:
                if on_progress:
                    on_progress("screen_reject", {
                        "candidate": chash,
                        "screen_rate": screen_sc.task_success_rate,
                        "baseline_screen_rate": base_screen_rate})
                cand["_screen_rejected"] = True
                continue
        full_sc = await _run_candidate(
            cfg, reg, agent_id, cand["system_prompt"], suite_id,
            model=cand.get("model", ""), version=version, client=client,
            judge_client=judge_client, on_progress=on_progress)
        out.append((cand, full_sc))
    return out


async def _run_candidate(cfg, reg, agent_id, system_prompt, suite_id, *,
                         model="", version=None, client=None, judge_client=None,
                         on_progress=None):
    """Run+score one prompt on one (sub-)suite via the standard chain. Isolated so
    tests can monkeypatch a single seam (mirrors ``optimizer._score_prompt``)."""
    adapter = ops.build_adapter(
        cfg, variant="reference", agent_id=agent_id,
        system_prompt=system_prompt, model=model, client=client)
    return await ops.run_and_score_op(
        cfg, reg, adapter, suite_id, version, on_progress,
        judge_client=judge_client or client)


# -- 4. gate -----------------------------------------------------------------

def gate(candidate_sc: Scorecard, baseline_sc: Scorecard, cfg: dict
         ) -> tuple[bool, str]:
    """Decide whether a candidate should be PROMOTED over the baseline.

    Promote iff ALL hold:
      1. task_success_rate STRICTLY improves (the reused
         :func:`evaluate_candidate` regression gate over the paired A/B stats);
      2. no per-criterion mean regresses by more than ``learning.epsilon`` (a
         small tolerance on top of the significance veto — catches a real but
         sub-significant drop);
      3. mean cost within ``learning.max_cost_multiplier`` × the baseline's;
      4. p95 latency within ``learning.max_latency_multiplier`` × the baseline's.

    Reuses ``compare_scorecards`` for the paired deltas. Returns
    ``(promote, reason)``."""
    lc = (cfg.get("learning") or {})
    epsilon = float(lc.get("epsilon", _DEFAULT_EPSILON))
    max_cost_mult = float(lc.get("max_cost_multiplier", _DEFAULT_MAX_COST_MULT))
    max_lat_mult = float(lc.get("max_latency_multiplier", _DEFAULT_MAX_LATENCY_MULT))

    comp = compare_scorecards(
        "learn-gate", baseline_sc, candidate_sc,
        ABVariant(label="baseline", agent_id=baseline_sc.agent_id),
        ABVariant(label="candidate", agent_id=candidate_sc.agent_id))

    accept, regressions, reason = evaluate_candidate(comp)
    if not accept:
        return False, reason

    # (2) ε-tolerance per-criterion floor (beyond the significance veto)
    base_means = baseline_sc.per_criterion_means
    cand_means = candidate_sc.per_criterion_means
    eps_drops = [
        (cid, cand_means[cid] - base_means[cid])
        for cid in base_means
        if cid in cand_means and (cand_means[cid] - base_means[cid]) < -epsilon
    ]
    if eps_drops:
        worst = ", ".join(f"{c} ({d:+.2f})" for c, d in eps_drops)
        return False, (f"rejected: per-criterion mean dropped beyond "
                       f"epsilon={epsilon:.2f} on {worst}")

    # (3) cost budget
    if baseline_sc.mean_cost_usd > 0 and max_cost_mult > 0:
        if candidate_sc.mean_cost_usd > baseline_sc.mean_cost_usd * max_cost_mult:
            return False, (
                f"rejected: mean cost {candidate_sc.mean_cost_usd:.4f} exceeds "
                f"{max_cost_mult:g}x baseline {baseline_sc.mean_cost_usd:.4f}")

    # (4) latency budget
    if baseline_sc.p95_latency_ms > 0 and max_lat_mult > 0:
        if candidate_sc.p95_latency_ms > baseline_sc.p95_latency_ms * max_lat_mult:
            return False, (
                f"rejected: p95 latency {candidate_sc.p95_latency_ms:.0f}ms "
                f"exceeds {max_lat_mult:g}x baseline "
                f"{baseline_sc.p95_latency_ms:.0f}ms")

    return True, reason


# -- 5. promote --------------------------------------------------------------

def _high_severity(cfg: dict, suite_id: str) -> bool:
    """Is this suite's domain marked high-severity in
    ``learning.high_severity_domains``? Matches a domain token appearing anywhere
    in the suite_id (case-insensitive substring), so ``refunds-suite`` matches the
    ``refunds`` domain."""
    domains = ((cfg.get("learning") or {}).get("high_severity_domains") or [])
    sid = (suite_id or "").lower()
    return any(str(d).lower() in sid for d in domains if str(d).strip())


def promote(reg: Registry, cfg: dict, candidate: dict, *, agent_id: str,
            parent_hash: str, suite_id: str, scorecard_ids: list[str],
            reason: str) -> AgentConfig:
    """Persist a PROMOTED candidate to the config ledger with parent-hash lineage.

    If the suite's domain is high-severity (``learning.high_severity_domains``)
    the row lands ``pending_approval`` — NOT activated — until a human runs
    ``agenttic learn approve <hash>``. Returns the persisted :class:`AgentConfig`."""
    pending = _high_severity(cfg, suite_id)
    status = "pending_approval" if pending else "promoted"
    cfg_reason = reason
    if pending:
        cfg_reason = (reason + " | HIGH-SEVERITY domain: blocked on "
                      "`agenttic learn approve`").strip()
    ac = AgentConfig(
        agent_id=agent_id, agent_config_hash=candidate["agent_config_hash"],
        parent_hash=parent_hash, diff_summary=candidate.get("changelog", ""),
        scorecard_ids=list(scorecard_ids), status=status, reason=cfg_reason,
        payload={"system_prompt": candidate["system_prompt"],
                 "model": candidate.get("model", ""),
                 "rationale": candidate.get("rationale", ""),
                 "changelog": candidate.get("changelog", ""),
                 "suite_id": suite_id})
    reg.save_agent_config(ac)
    return ac


def _record_rejection(reg: Registry, candidate: dict, *, agent_id: str,
                      parent_hash: str, suite_id: str,
                      scorecard_ids: list[str], reason: str) -> AgentConfig:
    """Persist a REJECTED candidate (auditable search history). Same ledger, with
    ``status="rejected"`` + the gate's reason."""
    ac = AgentConfig(
        agent_id=agent_id, agent_config_hash=candidate["agent_config_hash"],
        parent_hash=parent_hash, diff_summary=candidate.get("changelog", ""),
        scorecard_ids=list(scorecard_ids), status="rejected", reason=reason,
        payload={"system_prompt": candidate["system_prompt"],
                 "model": candidate.get("model", ""),
                 "rationale": candidate.get("rationale", ""),
                 "changelog": candidate.get("changelog", ""),
                 "suite_id": suite_id})
    reg.save_agent_config(ac)
    return ac


# -- orchestrator ------------------------------------------------------------

async def run_learning(
    reg: Registry,
    cfg: dict,
    agent_id: str,
    suite_id: str,
    *,
    rounds: int = 1,
    candidates_per_round: int = 3,
    baseline_prompt: str = "",
    model: str = "",
    version: int | None = None,
    proposer=None,
    proposer_client=None,
    client=None,
    judge_client=None,
    feedback=None,
    persist_run: bool = True,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Run the learning loop and return a summary dict.

    Per round: score the current-best config → build the dossier (judge + human
    feedback) → propose N candidates → screen+evaluate → gate → promote the best
    survivor (or record every candidate's rejection). The promoted config becomes
    the parent for the next round, so the ledger records the full baseline→latest
    chain. An :class:`OptimizationRun` (lineage of :class:`PromptVersion`) is also
    persisted for the existing run views.

    Returns ``{"promoted": [...], "rejected": [...], "baseline_hash", "rounds",
    "run_id", "pending": [...]}`` where each list holds ``AgentConfig``\\ s."""
    import uuid

    rounds = max(1, min(rounds, 10))
    candidates_per_round = max(1, min(candidates_per_round, 8))
    run_id = uuid.uuid4().hex[:12]

    suite, cases = reg.get_suite(suite_id, version)
    suite_version = suite.version
    rubric = reg.get_rubric(cases[0].rubric_id)

    if proposer is None:
        proposer = PromptOptimizer(
            model=(cfg.get("models", {}).get("optimizer")
                   or cfg["models"]["judge_strong"]),
            client=proposer_client or judge_client or client, cfg=cfg)

    feedback = feedback if feedback is not None else reg.feedback_for(agent_id)

    promoted: list[AgentConfig] = []
    rejected: list[AgentConfig] = []
    pending: list[AgentConfig] = []

    # baseline config (the root of the lineage)
    best_prompt = baseline_prompt
    best_hash = config_hash_for(agent_id, best_prompt, model)
    best_sc = await _run_candidate(
        cfg, reg, agent_id, best_prompt, suite_id, model=model, version=version,
        client=client, judge_client=judge_client, on_progress=on_progress)

    baseline_cfg = AgentConfig(
        agent_id=agent_id, agent_config_hash=best_hash, parent_hash="",
        diff_summary="baseline", scorecard_ids=[best_sc.scorecard_id],
        status="promoted", reason="baseline config",
        payload={"system_prompt": best_prompt, "model": model,
                 "changelog": "baseline", "suite_id": suite_id})
    try:
        reg.save_agent_config(baseline_cfg)
    except Exception:
        baseline_cfg = reg.get_agent_config(best_hash)

    run = OptimizationRun(
        run_id=run_id, agent_id=agent_id, suite_id=suite_id,
        suite_version=suite_version, rounds_requested=rounds,
        candidates_per_round=candidates_per_round,
        n_train=len(cases), baseline_prompt=best_prompt, best_prompt=best_prompt,
        baseline_train_rate=best_sc.task_success_rate,
        best_train_rate=best_sc.task_success_rate)
    run.lineage.append(PromptVersion(
        version=0, system_prompt=best_prompt, parent_version=None,
        rationale="baseline", train_success_rate=best_sc.task_success_rate,
        train_scorecard_id=best_sc.scorecard_id))
    best_version = 0
    total_cost = best_sc.total_cost_usd + best_sc.total_scoring_cost_usd

    for rnd in range(1, rounds + 1):
        dossier = collect_failures(best_sc, reg.traces(agent_id), feedback)
        if on_progress:
            on_progress("dossier", {"round": rnd,
                                    "n_failing": dossier.get("n_failing", 0),
                                    "n_human_corrections":
                                        dossier.get("n_human_corrections", 0)})
        candidates = propose(
            dossier, {"agent_id": agent_id, "system_prompt": best_prompt,
                      "model": model}, candidates_per_round, proposer=proposer)
        total_cost += getattr(proposer, "last_cost_usd", 0.0) or 0.0
        for c in candidates:
            c["baseline_prompt"] = best_prompt

        evaluated = await evaluate(
            candidates, reg, cfg, suite_id, agent_id=agent_id, version=version,
            baseline_sc=best_sc, client=client, judge_client=judge_client,
            on_progress=on_progress)

        survivors: list[tuple[dict, Scorecard, str]] = []
        gated_hashes = {c["agent_config_hash"] for c, _ in evaluated}
        for cand, cand_sc in evaluated:
            total_cost += cand_sc.total_cost_usd + cand_sc.total_scoring_cost_usd
            promote_ok, reason = gate(cand_sc, best_sc, cfg)
            if promote_ok:
                survivors.append((cand, cand_sc, reason))
            else:
                rejected.append(_record_rejection(
                    reg, cand, agent_id=agent_id, parent_hash=best_hash,
                    suite_id=suite_id, scorecard_ids=[cand_sc.scorecard_id],
                    reason=reason))
        # candidates dropped at the cheap screen are rejections too
        for cand in candidates:
            if cand.get("_screen_rejected") and \
                    cand["agent_config_hash"] not in gated_hashes:
                rejected.append(_record_rejection(
                    reg, cand, agent_id=agent_id, parent_hash=best_hash,
                    suite_id=suite_id, scorecard_ids=[],
                    reason="rejected at cheap screen: worse than baseline on the "
                           "subset (skipped full-suite run)"))

        if not survivors:
            if on_progress:
                on_progress("round_done", {"round": rnd, "chosen": None})
            continue

        # adopt the best survivor (highest full-suite success rate)
        survivors.sort(key=lambda t: -t[1].task_success_rate)
        cand, cand_sc, reason = survivors[0]
        ac = promote(reg, cfg, cand, agent_id=agent_id, parent_hash=best_hash,
                     suite_id=suite_id, scorecard_ids=[cand_sc.scorecard_id],
                     reason=reason)
        if ac.status == "pending_approval":
            pending.append(ac)
        else:
            promoted.append(ac)
        # record the other survivors as rejected (not adopted this round)
        for other_cand, other_sc, _ in survivors[1:]:
            rejected.append(_record_rejection(
                reg, other_cand, agent_id=agent_id, parent_hash=best_hash,
                suite_id=suite_id, scorecard_ids=[other_sc.scorecard_id],
                reason="not adopted: a stronger candidate won this round"))

        # the promoted config becomes the parent for the next round
        best_prompt = cand["system_prompt"]
        best_hash = cand["agent_config_hash"]
        best_sc = cand_sc
        best_version += 1
        run.best_prompt = best_prompt
        run.best_version = best_version
        run.best_train_rate = cand_sc.task_success_rate
        run.lineage.append(PromptVersion(
            version=best_version, system_prompt=best_prompt,
            parent_version=best_version - 1, rationale=cand.get("rationale", ""),
            train_success_rate=cand_sc.task_success_rate,
            train_scorecard_id=cand_sc.scorecard_id))
        if on_progress:
            on_progress("round_done", {"round": rnd, "chosen": best_hash,
                                       "status": ac.status})

    run.total_cost_usd = round(total_cost, 6)
    run.status = "succeeded"
    if persist_run:
        reg.save_optimization_run(run)

    return {"run_id": run_id, "rounds": rounds, "baseline_hash":
            baseline_cfg.agent_config_hash, "promoted": promoted,
            "rejected": rejected, "pending": pending}


# -- preference export -------------------------------------------------------

def export_preferences(reg: Registry, agent_id: str, *, suite_id: str | None = None
                       ) -> list[dict]:
    """Build preference pairs for offline preference tuning (we build the export,
    not the training).

    For each scored scorecard of the agent, join each run's trace with its score,
    then group by test case. Within a case, every higher-scoring trace is paired
    against every lower-scoring one as ``(chosen, rejected)`` — the standard
    preference-pair shape. Traces without a resolvable body are skipped.

    Returns a list of dict pairs; ``write_preferences_jsonl`` serializes them."""
    scorecards = reg.scorecards_for(agent_id, suite_id)
    # gather (test_id -> [(trace, passed, score)])
    by_case: dict[str, list[dict]] = {}
    for sc in scorecards:
        for r in sc.run_scores:
            if r.scoring_error is not None:
                continue
            score = (sum(cs.score for cs in r.criterion_scores) /
                     len(r.criterion_scores)) if r.criterion_scores else \
                (1.0 if r.passed else 0.0)
            body = ""
            try:
                tr = reg.get_trace(r.trace_id)
                body = tr.final_output
            except NotFoundError:
                body = ""
            by_case.setdefault(r.test_id, []).append({
                "trace_id": r.trace_id, "output": body,
                "passed": r.passed, "score": round(score, 4),
                "scorecard_id": sc.scorecard_id})

    pairs: list[dict] = []
    for test_id, items in by_case.items():
        for i in range(len(items)):
            for j in range(len(items)):
                a, b = items[i], items[j]
                if a["score"] <= b["score"]:
                    continue
                pairs.append({
                    "test_id": test_id, "agent_id": agent_id,
                    "chosen": {"trace_id": a["trace_id"], "output": a["output"],
                               "score": a["score"]},
                    "rejected": {"trace_id": b["trace_id"], "output": b["output"],
                                 "score": b["score"]},
                    "margin": round(a["score"] - b["score"], 4)})
    return pairs


def write_preferences_jsonl(pairs: list[dict], path) -> int:
    """Write preference pairs as JSONL (one JSON object per line). Returns the
    number of lines written."""
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair) + "\n")
    return len(pairs)


# -- helpers -----------------------------------------------------------------

def _rubric_of(scorecard: Scorecard, traces):
    """Resolve the rubric for reflection. Prefer a live registry lookup via the
    scorecard's rubric_id; degrade to None (reflection then yields an empty
    gradient, still valid)."""
    # reflect_on_failures only needs criterion descriptions; a minimal Rubric
    # built from the scorecard's criteria suffices when no registry is at hand.
    from agenttic.schema.rubric import Criterion, Rubric
    crit_ids = sorted(scorecard.per_criterion_means)
    if not crit_ids:
        # derive from run scores
        crit_ids = sorted({cs.criterion_id for r in scorecard.run_scores
                           for cs in r.criterion_scores})
    if not crit_ids:
        return None
    return Rubric(
        rubric_id=scorecard.rubric_id or "r",
        version=scorecard.rubric_version or 1,
        criteria=[Criterion(criterion_id=c, description=c, scorer="judge",
                            scale="binary", anchors={"pass": "ok", "fail": "no"})
                  for c in crit_ids])


def _cases_of(scorecard: Scorecard, traces):
    """Minimal TestCase list for reflection (needs test_id + task_description).
    Built from the scorecard's run scores; task text pulled from a matching trace
    when available."""
    from agenttic.schema.testcase import TestCase
    task_by_id: dict[str, str] = {}
    for tr in traces or []:
        tid = getattr(tr, "test_case_id", None)
        if tid:
            task_by_id.setdefault(tid, (getattr(tr, "final_output", "") or "")[:80])
    cases = []
    for r in scorecard.run_scores:
        cases.append(TestCase(
            test_id=r.test_id, suite_id=scorecard.suite_id,
            version=scorecard.suite_version or 1,
            task_description=task_by_id.get(r.test_id, r.test_id) or r.test_id,
            input={}, rubric_id=scorecard.rubric_id or "r"))
    return cases
