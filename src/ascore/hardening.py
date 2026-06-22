"""Failure-to-benchmark hardening loop — turn caught failures into permanent
regression cases, re-run to prove the fix held, and report the regression delta.

The flow ("hardening, not just measuring"):

  1. **Capture/promote** — take the failing (not errored) cases from a scorecard
     and promote them into a per-agent *regression suite*. The suite is created
     on first promotion and version-bumped (append-only, consistent with the
     registry) on every later promotion. Near-identical cases are de-duplicated
     so the suite doesn't bloat. Every promoted case keeps a provenance manifest
     recording the original case + *why* it failed.

  2. **Re-run + delta** — run the regression suite (reusing the normal
     run/score/scorecard plumbing) and compare against the prior regression
     scorecard, reporting improved / regressed / same / new per case. Errored
     cases are excluded from the verdict (errored != failed).

These are pure ops (registry + adapter only); the route layer and the CLI both
call them. Hard rules stay where they live — the human gate in the harness and
judge-model separation in scoring are untouched.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from ascore import ops
from ascore.registry.sqlite_store import NotFoundError, Registry
from ascore.schema.scorecard import RunScore, Scorecard
from ascore.schema.testcase import TestCase, TestSuite

REGRESSION_PREFIX = "regress--"
_MANIFEST_KIND = "regression_suite"


# -- identity & provenance ---------------------------------------------------

def regression_suite_id(agent_id: str, source_suite_id: str) -> str:
    """Deterministic id for an agent's regression suite for one source suite.
    Tenant scoping is handled by the registry, so this need only be unique within
    a tenant."""
    return f"{REGRESSION_PREFIX}{agent_id}--{source_suite_id}"


def fingerprint(case: TestCase) -> str:
    """Stable content hash used to de-dupe near-identical cases. Two cases with
    the same task, input and rubric are treated as duplicates even if their
    test_ids differ (e.g. promoted from two different scorecards)."""
    payload = json.dumps(
        {"task": (case.task_description or "").strip().lower(),
         "input": case.input or {},
         "rubric_id": case.rubric_id},
        sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def failure_reason(run: RunScore) -> str:
    """Human-readable 'why it failed', derived from the run's criterion scores."""
    misses = [f"{cs.criterion_id}={cs.score:g}"
              for cs in run.criterion_scores if cs.score < 1.0]
    if misses:
        return "failed criteria: " + ", ".join(misses)
    return "task not successful (below pass threshold)"


def _decode_manifest(business_context: str) -> dict:
    """Parse the provenance manifest embedded in a regression suite's
    business_context. Tolerant: a non-JSON / legacy context yields an empty
    manifest so the suite still functions."""
    try:
        data = json.loads(business_context)
        if isinstance(data, dict) and data.get("kind") == _MANIFEST_KIND:
            data.setdefault("cases", {})
            return data
    except (ValueError, TypeError):
        pass
    return {"kind": _MANIFEST_KIND, "cases": {}}


def _encode_manifest(manifest: dict) -> str:
    return json.dumps(manifest, default=str)


# -- capture / promote -------------------------------------------------------

def _failing_runs(sc: Scorecard, test_ids: Optional[list[str]]) -> list[RunScore]:
    """Failing, non-errored runs from a scorecard. Errored runs are excluded by
    convention (errored != failed). An optional test_ids allowlist narrows the
    selection to an explicit subset the caller chose to promote."""
    wanted = set(test_ids) if test_ids else None
    seen: set[str] = set()
    out: list[RunScore] = []
    for run in sc.run_scores:
        if run.scoring_error is not None:      # errored: not a real failure
            continue
        if run.passed:                          # passed: nothing to harden
            continue
        if wanted is not None and run.test_id not in wanted:
            continue
        if run.test_id in seen:                 # one card may repeat a case (k>1)
            continue
        seen.add(run.test_id)
        out.append(run)
    return out


def promote_failures_op(
    reg: Registry,
    scorecard_id: str,
    *,
    test_ids: Optional[list[str]] = None,
    source: str = "scorecard",
) -> dict:
    """Capture failing cases from a scorecard into the agent's regression suite.

    Create-or-append, version-bumped, append-only: the first promotion creates
    v1; later promotions read the latest version, merge in the new (de-duped)
    cases and save the next version. Returns a summary of what was promoted,
    skipped (duplicates / already present) and excluded (errored)."""
    sc = reg.get_scorecard(scorecard_id)
    source_suite, source_cases = reg.get_suite(sc.suite_id, sc.suite_version)
    case_by_id = {c.test_id: c for c in source_cases}

    failing = _failing_runs(sc, test_ids)
    errored = sorted({r.test_id for r in sc.run_scores if r.scoring_error is not None})

    reg_id = regression_suite_id(sc.agent_id, sc.suite_id)

    # Load the existing regression suite (if any) to append onto.
    try:
        existing_suite, existing_cases = reg.get_suite(reg_id)
        base_version = existing_suite.version
        manifest = _decode_manifest(existing_suite.business_context)
    except NotFoundError:
        existing_cases = []
        base_version = 0
        manifest = {"kind": _MANIFEST_KIND, "source_suite_id": sc.suite_id,
                    "agent_id": sc.agent_id, "cases": {}}

    new_version = base_version + 1
    kept: list[TestCase] = [
        c.model_copy(update={"suite_id": reg_id, "version": new_version})
        for c in existing_cases
    ]
    existing_ids = {c.test_id for c in kept}
    existing_prints = {fingerprint(c) for c in kept}

    added: list[str] = []
    skipped: list[str] = []
    for run in failing:
        original = case_by_id.get(run.test_id)
        if original is None:                    # case missing from source suite
            skipped.append(run.test_id)
            continue
        fp = fingerprint(original)
        if run.test_id in existing_ids or fp in existing_prints:
            skipped.append(run.test_id)         # already promoted / near-identical
            continue
        promoted = original.model_copy(
            update={"suite_id": reg_id, "version": new_version})
        kept.append(promoted)
        existing_ids.add(run.test_id)
        existing_prints.add(fp)
        added.append(run.test_id)
        manifest["cases"][run.test_id] = {
            "why": failure_reason(run),
            "source": source,
            "source_scorecard_id": scorecard_id,
            "source_suite_id": sc.suite_id,
            "source_suite_version": sc.suite_version,
            "fingerprint": fp,
        }

    if not added:
        # Nothing new to harden — stay idempotent, do not bump the version.
        return {
            "regression_suite_id": reg_id,
            "version": base_version,
            "created": False,
            "added": [],
            "skipped_duplicates": skipped,
            "excluded_errored": errored,
            "total_cases": len(existing_cases),
            "source_scorecard_id": scorecard_id,
        }

    suite = TestSuite(
        suite_id=reg_id,
        version=new_version,
        business_context=_encode_manifest(manifest),
        test_ids=[c.test_id for c in kept],
        approved=True,  # promoted from already-approved cases; runnable immediately
    )
    reg.save_suite(suite, kept)
    return {
        "regression_suite_id": reg_id,
        "version": new_version,
        "created": base_version == 0,
        "added": added,
        "skipped_duplicates": skipped,
        "excluded_errored": errored,
        "total_cases": len(kept),
        "source_scorecard_id": scorecard_id,
    }


# -- re-run + delta ----------------------------------------------------------

def compute_regression_delta(prev: Optional[Scorecard], cur: Scorecard) -> dict:
    """Per-case verdict of ``cur`` vs ``prev`` regression scorecards.

    improved: failed-before, passes-now; regressed: passed-before, fails-now;
    same: unchanged; new: not present in prev; errored: excluded from the verdict.
    Pure — no I/O — so it is trivially testable."""
    prev_pass: dict[str, bool] = {}
    if prev is not None:
        for r in prev.run_scores:
            if r.scoring_error is None:
                prev_pass[r.test_id] = r.passed

    per_case: list[dict] = []
    counts = {"improved": 0, "regressed": 0, "same": 0, "new": 0, "errored": 0}
    paired_prev: list[bool] = []
    paired_cur: list[bool] = []
    for r in cur.run_scores:
        if r.scoring_error is not None:
            counts["errored"] += 1
            per_case.append({"test_id": r.test_id, "status": "errored",
                             "prev_passed": prev_pass.get(r.test_id),
                             "now_passed": None, "error": r.scoring_error})
            continue
        now = r.passed
        if r.test_id in prev_pass:
            was = prev_pass[r.test_id]
            paired_prev.append(was)
            paired_cur.append(now)
            if was == now:
                status = "same"
            elif now and not was:
                status = "improved"
            else:
                status = "regressed"
        else:
            status = "new"
        counts[status] += 1
        per_case.append({"test_id": r.test_id, "status": status,
                         "prev_passed": prev_pass.get(r.test_id),
                         "now_passed": now})

    mcnemar = None
    if len(paired_prev) >= 1 and any(a != b for a, b in zip(paired_prev, paired_cur)):
        try:
            from ascore.stats import mcnemar as _mcnemar
            mcnemar = _mcnemar(paired_prev, paired_cur).to_dict()
        except Exception:  # noqa: BLE001 — stats are advisory, never fatal
            mcnemar = None

    per_case.sort(key=lambda d: (
        {"regressed": 0, "improved": 1, "new": 2, "errored": 3, "same": 4}[d["status"]],
        d["test_id"]))
    return {
        "summary": counts,
        "n_cases": len(cur.run_scores),
        "per_case": per_case,
        "task_success_rate": cur.task_success_rate,
        "prev_task_success_rate": prev.task_success_rate if prev else None,
        "success_delta": (cur.task_success_rate - prev.task_success_rate)
                          if prev else None,
        "scorecard_id": cur.scorecard_id,
        "prev_scorecard_id": prev.scorecard_id if prev else None,
        "mcnemar": mcnemar,
    }


async def rerun_regression_op(
    cfg: dict,
    reg: Registry,
    regression_suite_id: str,
    *,
    variant: str = "reference",
    url: str = "",
    system_prompt: str = "",
    model: str = "",
    managed_agent_id: str = "",
    environment_id: str = "",
    headers: dict | None = None,
    client=None,
    judge_client=None,
    on_progress=None,
) -> dict:
    """Run a regression suite and return its scorecard + the delta vs the prior
    regression scorecard for the same agent. Reuses the standard run/score/
    scorecard + checkpoint + BYO-key plumbing via ``ops.run_and_score_op``."""
    suite, _cases = reg.get_suite(regression_suite_id)
    manifest = _decode_manifest(suite.business_context)
    agent_id = manifest.get("agent_id") or _agent_from_reg_id(regression_suite_id)

    # The most recent prior scorecard becomes the baseline for the delta.
    prior_cards = reg.scorecards_for(agent_id, regression_suite_id)
    prev = prior_cards[-1] if prior_cards else None

    adapter = ops.build_adapter(
        cfg, variant=variant, agent_id=agent_id, url=url,
        system_prompt=system_prompt, model=model,
        managed_agent_id=managed_agent_id, environment_id=environment_id,
        headers=headers, client=client)
    sc = await ops.run_and_score_op(
        cfg, reg, adapter, regression_suite_id,
        on_progress=on_progress, judge_client=judge_client or client)

    delta = compute_regression_delta(prev, sc)
    return {"regression_suite_id": regression_suite_id, "agent_id": agent_id,
            "suite_version": suite.version, "scorecard_id": sc.scorecard_id,
            "delta": delta}


def _agent_from_reg_id(reg_id: str) -> str:
    """Recover the agent_id from a regression suite id (fallback when the
    manifest lacks it). Mirrors ``regression_suite_id``'s shape."""
    if reg_id.startswith(REGRESSION_PREFIX):
        rest = reg_id[len(REGRESSION_PREFIX):]
        return rest.split("--", 1)[0]
    return reg_id


# -- discovery surfaces ------------------------------------------------------

def list_regression_suites(reg: Registry) -> list[dict]:
    """Every regression suite in the workspace with its latest delta summary."""
    out = []
    for s in reg.list_suites(prefix=REGRESSION_PREFIX):
        suite, _cases = reg.get_suite(s["suite_id"])
        manifest = _decode_manifest(suite.business_context)
        agent_id = manifest.get("agent_id") or _agent_from_reg_id(s["suite_id"])
        cards = reg.scorecards_for(agent_id, s["suite_id"])
        latest_delta = None
        if cards:
            prev = cards[-2] if len(cards) >= 2 else None
            latest_delta = compute_regression_delta(prev, cards[-1])["summary"]
        out.append({
            "regression_suite_id": s["suite_id"],
            "version": s["version"],
            "n_cases": s["n_cases"],
            "agent_id": agent_id,
            "source_suite_id": manifest.get("source_suite_id", ""),
            "runs": len(cards),
            "latest_delta": latest_delta,
            "latest_success_rate": cards[-1].task_success_rate if cards else None,
        })
    return out


def regression_detail(reg: Registry, regression_suite_id: str) -> dict:
    """Full view of one regression suite: its cases (+ why each was promoted),
    its scorecard history, and the delta of the latest run vs the prior one."""
    suite, cases = reg.get_suite(regression_suite_id)
    manifest = _decode_manifest(suite.business_context)
    agent_id = manifest.get("agent_id") or _agent_from_reg_id(regression_suite_id)
    cards = reg.scorecards_for(agent_id, regression_suite_id)

    history = [{"scorecard_id": c.scorecard_id, "suite_version": c.suite_version,
                "task_success_rate": c.task_success_rate,
                "created_at": c.created_at.isoformat(),
                "n_cases": len(c.run_scores),
                "errored": len(c.errored_test_ids)} for c in cards]
    latest_delta = None
    if cards:
        prev = cards[-2] if len(cards) >= 2 else None
        latest_delta = compute_regression_delta(prev, cards[-1])

    return {
        "regression_suite_id": regression_suite_id,
        "version": suite.version,
        "agent_id": agent_id,
        "source_suite_id": manifest.get("source_suite_id", ""),
        "approved": suite.approved,
        "cases": [{
            "test_id": c.test_id,
            "task_description": c.task_description,
            "tags": c.tags,
            "rubric_id": c.rubric_id,
            "provenance": manifest.get("cases", {}).get(c.test_id),
        } for c in cases],
        "history": history,
        "latest_delta": latest_delta,
    }


def promotion_candidates(reg: Registry) -> list[dict]:
    """Scorecards that have at least one failing (non-errored) case — the
    sources you can promote from. Regression suites' own scorecards are
    excluded so the surface stays focused on upstream failures."""
    suite_ids = [s["suite_id"] for s in reg.list_suites()
                 if not s["suite_id"].startswith(REGRESSION_PREFIX)]
    out = []
    for sc in reg.scorecards_in(suite_ids):
        failing = _failing_runs(sc, None)
        if not failing:
            continue
        out.append({
            "scorecard_id": sc.scorecard_id,
            "agent_id": sc.agent_id,
            "suite_id": sc.suite_id,
            "suite_version": sc.suite_version,
            "created_at": sc.created_at.isoformat(),
            "task_success_rate": sc.task_success_rate,
            "n_failing": len(failing),
            "failing_test_ids": [r.test_id for r in failing],
            "n_errored": len(sc.errored_test_ids),
        })
    out.sort(key=lambda d: d["created_at"], reverse=True)
    return out
