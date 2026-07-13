"""Prompt-optimizer — a self-improving system-prompt loop.

The platform's hardening differentiator: keep the model **frozen**, treat the
eval score as the reward, and iteratively improve the agent's SYSTEM PROMPT
against a suite. Each round:

  (a) run the current best prompt on the train split and score it;
  (b) read the FAILING criteria + judge rationales — the *textual gradient*;
  (c) ask an LLM (the tenant's BYO-key optimizer/judge client) to propose N
      candidate edited prompts targeting those failures;
  (d) A/B-evaluate each candidate vs the current best on the SAME train cases,
      reusing the paired stats of :func:`agenttic.ab.compare_scorecards`;
  (e) accept a candidate only on a *real* improvement with **regression
      protection** — a net pass-rate gain AND no criterion the edit
      significantly broke (the per-criterion paired bootstrap vetoes a prompt
      that fixes one thing and breaks another).

**Methodology.** This is reflective, score-guided prompt search in the lineage
of OPRO (Yang et al. 2023, *"Large Language Models as Optimizers"*) and ProTeGi
(Pryzant et al. 2023, *"Automatic Prompt Optimization with 'Gradient Descent'
and Beam Search"*) — DSPy-style: the judge rationales on failing cases are the
natural-language "gradient", an LLM applies it to edit the prompt, and the suite
score is the objective. Acceptance is empirical (paired significance), not the
optimizer's say-so.

**Overfitting guard.** A held-out slice of the suite is split off up front and
the optimizer NEVER sees it (no case in it feeds reflection or acceptance). The
baseline and the best prompt are scored on both splits, so a prompt that climbs
the train reward while flat/declining on held-out is visible as a positive
``overfit_gap`` — the single most important honesty check in any optimizer.

Everything is bounded and cost-aware: rounds × candidates × splits are capped,
the projected number of suite executions is surfaced before spend, and the run
records actual cost. Persistence is append-only in the registry (the run record
+ the full prompt lineage + every scorecard id).

Pure-ish: the loop only needs a registry + clients; the heavy lifting reuses the
existing run/score/aggregate ops, so the harness human gate and judge-model
separation (Hard Rule 4) apply unchanged.
"""

from __future__ import annotations

import json
import random
import uuid
from typing import Callable, Optional

from agenttic import ops
from agenttic.ab import compare_scorecards
from agenttic.registry.sqlite_store import NotFoundError, Registry
from agenttic.schema.ab import ABVariant
from agenttic.schema.optimization import (
    CandidateResult,
    OptimizationRound,
    OptimizationRun,
    PerCriterionRegression,
    PromptVersion,
)
from agenttic.schema.rubric import Rubric
from agenttic.schema.scorecard import Scorecard
from agenttic.schema.testcase import TestCase, TestSuite

ProgressFn = Callable[[str, dict], None]

OPTIM_PREFIX = "optim--"
_MAX_RATIONALES = 4         # sample judge rationales per failing criterion
_RATIONALE_TRUNC = 240


# -- split (the overfitting guard) -------------------------------------------

def split_suite(test_ids: list[str], heldout_fraction: float, seed: int
                ) -> tuple[list[str], list[str]]:
    """Deterministically partition case ids into (train, heldout).

    Sorted-then-seeded-shuffle so the split is stable for a given (suite, seed)
    — reproducible and auditable. At least one case stays in train; held-out is
    capped so train is never empty. Held-out can be empty only when the suite is
    a single case (nothing to hold out), which the caller surfaces."""
    ids = sorted(test_ids)
    n = len(ids)
    if n <= 1:
        return ids, []
    rng = random.Random(seed)
    shuffled = ids[:]
    rng.shuffle(shuffled)
    n_held = int(round(n * heldout_fraction))
    n_held = max(1, min(n_held, n - 1))   # keep >=1 in each split when n>=2
    heldout = sorted(shuffled[:n_held])
    train = sorted(shuffled[n_held:])
    return train, heldout


def _split_suite_id(run_id: str, kind: str) -> str:
    return f"{OPTIM_PREFIX}{run_id}--{kind}"


def materialize_split_suites(
    reg: Registry, suite_id: str, version: int,
    train_ids: list[str], heldout_ids: list[str], run_id: str,
) -> tuple[str, str | None]:
    """Persist the train/heldout splits as their own approved suites so the
    standard run/score plumbing can execute them directly. Returns
    (train_suite_id, heldout_suite_id|None). Cases keep their rubric_id, so the
    same rubric + judge score every split — the splits differ only in coverage."""
    _suite, cases = reg.get_suite(suite_id, version)
    by_id = {c.test_id: c for c in cases}

    def _make(kind: str, ids: list[str]) -> str | None:
        if not ids:
            return None
        sid = _split_suite_id(run_id, kind)
        sub = [by_id[t].model_copy(update={"suite_id": sid, "version": 1})
               for t in ids if t in by_id]
        suite = TestSuite(
            suite_id=sid, version=1,
            business_context=json.dumps({
                "kind": "optimization_split", "split": kind, "run_id": run_id,
                "source_suite_id": suite_id, "source_suite_version": version}),
            test_ids=[c.test_id for c in sub], approved=True)
        reg.save_suite(suite, sub)
        return sid

    return _make("train", train_ids), _make("heldout", heldout_ids)


# -- reflection (the textual gradient) ---------------------------------------

def reflect_on_failures(sc: Scorecard, rubric: Rubric, cases: list[TestCase]
                        ) -> dict:
    """Summarize *why* the current prompt fails on the train split — the
    natural-language gradient the optimizer edits against.

    Per failing (non-errored) case we collect the criteria it missed and the
    judge's rationale for each, then roll up by criterion (which fail most, with
    sample rationales + the offending task). Errored cases are excluded (a
    scoring outage is not a prompt failure)."""
    case_by_id = {c.test_id: c for c in cases}
    crit_desc = {c.criterion_id: c.description for c in rubric.criteria}

    per_criterion: dict[str, dict] = {}
    failing_cases: list[dict] = []
    for r in sc.run_scores:
        if r.scoring_error is not None or r.passed:
            continue
        case = case_by_id.get(r.test_id)
        task = (case.task_description if case else r.test_id) or r.test_id
        missed = []
        for cs in r.criterion_scores:
            if cs.score >= 1.0:
                continue
            missed.append(cs.criterion_id)
            bucket = per_criterion.setdefault(
                cs.criterion_id, {"criterion_id": cs.criterion_id,
                                  "description": crit_desc.get(cs.criterion_id, ""),
                                  "n_failed": 0, "rationales": []})
            bucket["n_failed"] += 1
            if cs.judge_rationale and len(bucket["rationales"]) < _MAX_RATIONALES:
                bucket["rationales"].append(
                    f"[{task[:80]}] {cs.judge_rationale[:_RATIONALE_TRUNC]}")
        failing_cases.append({"test_id": r.test_id, "task": task[:160],
                              "missed": missed})

    ranked = sorted(per_criterion.values(), key=lambda d: -d["n_failed"])
    return {"failing_criteria": [c["criterion_id"] for c in ranked],
            "per_criterion": ranked, "failing_cases": failing_cases,
            "n_failing": len(failing_cases)}


def build_optimizer_prompt(current_prompt: str, reflection: dict,
                           n_candidates: int) -> str:
    """The instruction handed to the optimizer LLM: here is the prompt under
    optimization, here is exactly where and why it fails, propose N edited
    prompts that fix those failures without dropping what already works."""
    lines = [
        "You are optimizing the SYSTEM PROMPT of an AI agent. The model is "
        "FROZEN; only the system prompt may change. Your objective is to raise "
        "the agent's pass rate on an evaluation suite.",
        "",
        "CURRENT SYSTEM PROMPT:",
        "<<<", current_prompt or "(empty — the agent has no system prompt yet)", ">>>",
        "",
        f"It is FAILING {reflection['n_failing']} evaluation case(s). The failures, "
        "grouped by the criterion that was missed (this is your gradient — edit "
        "the prompt to fix exactly these):",
    ]
    for c in reflection["per_criterion"]:
        lines.append(f"\n• criterion '{c['criterion_id']}': {c['description']} "
                     f"— missed in {c['n_failed']} case(s)")
        for rat in c["rationales"]:
            lines.append(f"    - judge said: {rat}")
    if not reflection["per_criterion"]:
        lines.append("\n(no per-criterion detail available; infer from the tasks)")
    lines += [
        "",
        f"Propose {n_candidates} DISTINCT edited system prompts. Each must be a "
        "COMPLETE replacement prompt (not a diff), should preserve the agent's "
        "working behaviour, and should add precise guidance targeting the "
        "failures above. Vary your strategies across candidates (e.g. explicit "
        "rules, worked examples, output-format constraints, self-checks).",
        "",
        'Respond with ONLY a JSON array of objects, each '
        '{"prompt": "<full system prompt>", "rationale": "<one sentence on what '
        'this edit targets>"} and nothing else.',
    ]
    return "\n".join(lines)


class PromptOptimizer:
    """Wraps the LLM call that proposes candidate prompts. Injected with the
    tenant's client (BYO-key); the model defaults to the configured optimizer
    model, falling back to the strong judge model. Robust JSON parsing with one
    retry, then it yields whatever it parsed (possibly empty — the round simply
    finds no candidate)."""

    SYSTEM = ("You are an expert prompt engineer performing reflective prompt "
              "optimization (OPRO/ProTeGi). You output only valid JSON.")

    def __init__(self, *, model: str, client=None, cfg: dict | None = None,
                 max_tokens: int = 4096, max_retries: int = 1):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.cfg = cfg
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.last_cost_usd = 0.0

    def propose(self, current_prompt: str, reflection: dict, n: int
                ) -> list[dict]:
        prompt = build_optimizer_prompt(current_prompt, reflection, n)
        self.last_cost_usd = 0.0
        last_raw = ""
        for _ in range(self.max_retries + 1):
            resp = self.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self.SYSTEM,
                messages=[{"role": "user", "content": prompt}])
            self.last_cost_usd += self._cost(resp)
            texts = [b.text for b in resp.content
                     if getattr(b, "type", "") == "text"]
            last_raw = "".join(texts)
            parsed = self._parse(last_raw, n)
            if parsed:
                return parsed
        return []

    def _cost(self, resp) -> float:
        usage = getattr(resp, "usage", None)
        if not self.cfg or usage is None:
            return 0.0
        from agenttic.pricing import token_cost
        return token_cost(self.cfg, self.model,
                          getattr(usage, "input_tokens", None),
                          getattr(usage, "output_tokens", None))

    @staticmethod
    def _parse(raw: str, n: int) -> list[dict]:
        """Extract a JSON array of {prompt, rationale}. Tolerant of code fences
        and surrounding prose; drops malformed entries; caps at n."""
        s = raw.strip()
        if "```" in s:
            # strip a ```json ... ``` fence if present
            parts = s.split("```")
            s = max(parts, key=len)
            if s.lstrip().startswith("json"):
                s = s.lstrip()[4:]
        start, end = s.find("["), s.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(s[start:end + 1])
        except (ValueError, TypeError):
            return []
        out = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and isinstance(item.get("prompt"), str) \
                    and item["prompt"].strip():
                out.append({"prompt": item["prompt"].strip(),
                            "rationale": str(item.get("rationale", ""))[:300]})
            if len(out) >= n:
                break
        return out


# -- run a prompt on a split -------------------------------------------------

async def _score_prompt(
    cfg: dict, reg: Registry, agent_id: str, system_prompt: str,
    suite_id: str, *, variant: str = "reference", model: str = "",
    url: str = "", headers: dict | None = None, client=None, judge_client=None,
    on_progress: ProgressFn | None = None,
) -> Scorecard:
    """Run one system prompt on one (sub-)suite and return its scorecard. Reuses
    the standard run→score→aggregate chain, so cost, budget caps, checkpoint/
    resume and judge separation all apply."""
    adapter = ops.build_adapter(
        cfg, variant=variant, agent_id=agent_id, system_prompt=system_prompt,
        model=model, url=url, headers=headers, client=client)
    return await ops.run_and_score_op(
        cfg, reg, adapter, suite_id, on_progress=on_progress,
        judge_client=judge_client or client)


# -- acceptance (regression protection) --------------------------------------

def evaluate_candidate(comparison) -> tuple[bool, list[PerCriterionRegression], str]:
    """Decide whether a candidate beats the current best with regression
    protection, from the paired A/B comparison.

    Accept iff (1) the net pass rate strictly improves on the SAME train cases
    AND (2) no criterion was *significantly* worsened (a significant negative
    per-criterion paired-bootstrap delta vetoes the edit — this is what stops a
    prompt that fixes one criterion while breaking another). Returns
    (accept, regressions, reason)."""
    regressions = [
        PerCriterionRegression(criterion_id=c.criterion_id, delta=c.delta,
                               p_value=c.p_value, n=c.n)
        for c in comparison.per_criterion if c.significant and c.delta < 0
    ]
    delta = comparison.success_delta
    if regressions:
        names = ", ".join(r.criterion_id for r in regressions)
        return False, regressions, (
            f"rejected: would significantly regress {names} "
            f"(net pass-rate delta {delta:+.0%})")
    if delta > 0:
        return True, [], (
            f"accepted: pass rate {comparison.success_rate_a:.0%} → "
            f"{comparison.success_rate_b:.0%} (+{delta:.0%}) on {comparison.n_paired} "
            "train case(s), no criterion regressed")
    if delta == 0:
        return False, [], (
            f"rejected: no pass-rate improvement (tied at "
            f"{comparison.success_rate_b:.0%} on {comparison.n_paired} case(s))")
    return False, [], (
        f"rejected: pass rate dropped {delta:.0%} on {comparison.n_paired} case(s)")


# -- cost projection ---------------------------------------------------------

def project_runs(rounds: int, candidates_per_round: int, has_heldout: bool
                 ) -> int:
    """Upper bound on suite executions: baseline on each split (1 or 2), then per
    round a baseline-on-train re-score + N candidates on train, plus an accepted
    candidate's held-out score. The cost warning surfaces this before any spend."""
    base = 2 if has_heldout else 1
    per_round = (1 + candidates_per_round) + (1 if has_heldout else 0)
    return base + rounds * per_round


# -- the loop ----------------------------------------------------------------

async def optimize(
    cfg: dict,
    reg: Registry,
    agent_id: str,
    suite_id: str,
    *,
    rounds: int = 2,
    candidates_per_round: int = 3,
    heldout_fraction: float = 0.3,
    seed: int = 1234,
    baseline_prompt: str = "",
    version: int | None = None,
    variant: str = "reference",
    model: str = "",
    url: str = "",
    headers: dict | None = None,
    client=None,
    judge_client=None,
    optimizer=None,
    optimizer_client=None,
    max_agent_runs: int = 60,
    run_id: str | None = None,
    persist: bool = True,
    on_progress: ProgressFn | None = None,
) -> OptimizationRun:
    """Run the bounded prompt-optimization loop and return the
    :class:`OptimizationRun` (best prompt + improvement + train/heldout).

    ``rounds``, ``candidates_per_round`` and ``max_agent_runs`` bound the spend;
    a candidate is adopted only on a paired improvement with no significant
    per-criterion regression; the held-out split is scored for the baseline and
    each newly-adopted prompt so overfitting is reported, never hidden."""
    rounds = max(1, min(rounds, 10))
    candidates_per_round = max(1, min(candidates_per_round, 8))
    heldout_fraction = min(max(heldout_fraction, 0.0), 0.5)
    run_id = run_id or uuid.uuid4().hex[:12]

    suite, cases = reg.get_suite(suite_id, version)
    suite_version = suite.version
    rubric = reg.get_rubric(cases[0].rubric_id)

    train_ids, heldout_ids = split_suite(
        [c.test_id for c in cases], heldout_fraction, seed)
    train_sid, heldout_sid = materialize_split_suites(
        reg, suite_id, suite_version, train_ids, heldout_ids, run_id)
    has_heldout = heldout_sid is not None

    run = OptimizationRun(
        run_id=run_id, agent_id=agent_id, suite_id=suite_id,
        suite_version=suite_version, rounds_requested=rounds,
        candidates_per_round=candidates_per_round,
        heldout_fraction=heldout_fraction, seed=seed,
        n_train=len(train_ids), n_heldout=len(heldout_ids),
        train_test_ids=train_ids, heldout_test_ids=heldout_ids,
        baseline_prompt=baseline_prompt, best_prompt=baseline_prompt)

    projected = project_runs(rounds, candidates_per_round, has_heldout)
    if on_progress:
        on_progress("cost_projection", {
            "projected_agent_runs": projected, "max_agent_runs": max_agent_runs,
            "n_train": len(train_ids), "n_heldout": len(heldout_ids),
            "note": "Optimization runs the suite many times; this is the upper "
                    "bound on suite executions (your own key pays for each)."})

    optimizer = optimizer or PromptOptimizer(
        model=(cfg.get("models", {}).get("optimizer")
               or cfg["models"]["judge_strong"]),
        client=optimizer_client or judge_client or client, cfg=cfg)

    n_runs = 0
    total_cost = 0.0

    def _account(sc: Scorecard) -> None:
        nonlocal n_runs, total_cost
        n_runs += 1
        total_cost += sc.total_cost_usd + sc.total_scoring_cost_usd

    async def _run_prompt(prompt: str, sid: str) -> Scorecard:
        sc = await _score_prompt(
            cfg, reg, agent_id, prompt, sid, variant=variant, model=model,
            url=url, headers=headers, client=client, judge_client=judge_client,
            on_progress=on_progress)
        _account(sc)
        return sc

    # baseline on both splits
    best_prompt = baseline_prompt
    best_train_sc = await _run_prompt(best_prompt, train_sid)
    run.baseline_train_rate = best_train_sc.task_success_rate
    run.best_train_rate = best_train_sc.task_success_rate
    baseline_heldout_sc = (await _run_prompt(best_prompt, heldout_sid)
                           if has_heldout else None)
    if baseline_heldout_sc is not None:
        run.baseline_heldout_rate = baseline_heldout_sc.task_success_rate
        run.best_heldout_rate = baseline_heldout_sc.task_success_rate

    run.lineage.append(PromptVersion(
        version=0, system_prompt=best_prompt, parent_version=None,
        rationale="baseline", train_success_rate=run.baseline_train_rate,
        heldout_success_rate=run.baseline_heldout_rate,
        train_scorecard_id=best_train_sc.scorecard_id,
        heldout_scorecard_id=(baseline_heldout_sc.scorecard_id
                              if baseline_heldout_sc else "")))

    base_variant = ABVariant(label="current-best", agent_id=agent_id,
                             system_prompt=best_prompt, model=model)
    best_version = 0

    for rnd in range(1, rounds + 1):
        if n_runs + (1 + candidates_per_round) > max_agent_runs:
            if on_progress:
                on_progress("budget_stop", {
                    "round": rnd, "n_runs": n_runs,
                    "reason": "run cap reached; stopping before next round"})
            break

        reflection = reflect_on_failures(best_train_sc, rubric, cases)
        round_rec = OptimizationRound(
            round=rnd, baseline_version=best_version,
            baseline_train_rate=best_train_sc.task_success_rate,
            failing_criteria=reflection["failing_criteria"])

        if reflection["n_failing"] == 0:
            round_rec.candidates = []
            run.rounds.append(round_rec)
            if on_progress:
                on_progress("round_done", {"round": rnd, "chosen": None,
                                           "reason": "no failures left to fix"})
            break

        if on_progress:
            on_progress("propose", {"round": rnd,
                                    "failing_criteria": reflection["failing_criteria"],
                                    "n_failing": reflection["n_failing"]})
        proposals = optimizer.propose(best_prompt, reflection, candidates_per_round)
        total_cost += getattr(optimizer, "last_cost_usd", 0.0) or 0.0

        evaluated: list[tuple[CandidateResult, str, Scorecard]] = []
        for i, prop in enumerate(proposals):
            if n_runs + 1 > max_agent_runs:
                break
            cand_sc = await _run_prompt(prop["prompt"], train_sid)
            cand_variant = ABVariant(label=f"cand-{rnd}.{i}", agent_id=agent_id,
                                     system_prompt=prop["prompt"], model=model)
            comp = compare_scorecards(
                f"{run_id}-r{rnd}c{i}", best_train_sc, cand_sc,
                base_variant, cand_variant)
            accept, regressions, reason = evaluate_candidate(comp)
            cr = CandidateResult(
                index=i, system_prompt=prop["prompt"],
                rationale=prop.get("rationale", ""),
                scorecard_id=cand_sc.scorecard_id, comparison_id=comp.comparison_id,
                train_success_rate=comp.success_rate_b,
                success_delta=comp.success_delta,
                mcnemar_p=comp.mcnemar.get("p_value"),
                regressions=regressions, accepted=accept, reason=reason)
            round_rec.candidates.append(cr)
            if accept:
                evaluated.append((cr, prop["prompt"], cand_sc))
            if on_progress:
                on_progress("candidate", {"round": rnd, "index": i,
                                          "accepted": accept, "reason": reason})

        # adopt the best accepted candidate (highest train rate; the comparison
        # already guaranteed each beats the incumbent with no regression)
        if evaluated:
            evaluated.sort(key=lambda t: (-t[0].train_success_rate,
                                          -t[0].success_delta))
            chosen, chosen_prompt, chosen_sc = evaluated[0]
            round_rec.chosen_index = chosen.index
            best_version += 1
            best_prompt = chosen_prompt
            best_train_sc = chosen_sc
            base_variant = ABVariant(label="current-best", agent_id=agent_id,
                                     system_prompt=best_prompt, model=model)
            run.best_prompt = best_prompt
            run.best_version = best_version
            run.best_train_rate = chosen_sc.task_success_rate

            heldout_sc = None
            if has_heldout and n_runs + 1 <= max_agent_runs:
                heldout_sc = await _run_prompt(best_prompt, heldout_sid)
                run.best_heldout_rate = heldout_sc.task_success_rate
            run.lineage.append(PromptVersion(
                version=best_version, system_prompt=best_prompt,
                parent_version=round_rec.baseline_version,
                rationale=chosen.rationale,
                train_success_rate=chosen_sc.task_success_rate,
                heldout_success_rate=(heldout_sc.task_success_rate
                                      if heldout_sc else None),
                train_scorecard_id=chosen_sc.scorecard_id,
                heldout_scorecard_id=(heldout_sc.scorecard_id
                                      if heldout_sc else "")))

        run.rounds.append(round_rec)
        if on_progress:
            on_progress("round_done", {"round": rnd,
                                       "chosen": round_rec.chosen_index,
                                       "best_train_rate": run.best_train_rate})

    run.total_cost_usd = round(total_cost, 6)
    run.n_agent_runs = n_runs
    run.status = "succeeded"
    if persist:
        reg.save_optimization_run(run)
    return run
