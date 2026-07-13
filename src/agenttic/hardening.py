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
from ascore.schema.trace import Trace

REGRESSION_PREFIX = "regress--"
_MANIFEST_KIND = "regression_suite"

# A live catch has no scorecard/source-suite — production traffic carries no
# scripted test case. Live-promoted cases land in a per-agent regression suite
# keyed by this sentinel so they stay *distinct* from scorecard-derived suites.
LIVE_SOURCE_SUITE = "live"
# Mean live score (over the live-tagged criteria) at/below which a sampled
# production trace counts as a caught failure worth promoting.
DEFAULT_LIVE_CATCH_THRESHOLD = 0.5


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


# -- live-monitor catches as a promotion source ------------------------------
#
# The scorecard path above promotes failures that already have a rubric and (via
# the source suite) trustworthy ground truth. The *live* path is different: a
# caught production trace has the agent's input and an observed sub-threshold
# score, but NO scripted expected/rubric. So we reconstruct only what the trace
# actually carries (the input) and refuse to invent the rest — every live-
# promoted case is tagged ``needs-review`` and its ``expected`` stays ``None``
# rather than fabricating a ground truth we don't have.

def live_test_id(trace_id: str) -> str:
    """Stable, readable test id for a case reconstructed from a live trace."""
    return f"live-{trace_id[:12]}"


def _live_catch(entry: dict, threshold: float) -> Optional[dict]:
    """Classify one trace's sampled live scores as a *catch* (mean strictly
    below ``threshold``) or not. Returns a catch descriptor, or None for a
    healthy trace (so non-catches are never promotable)."""
    scores = entry.get("scores") or {}
    if not scores:
        return None
    mean = sum(scores.values()) / len(scores)
    if mean >= threshold:
        return None
    failing = sorted(cid for cid, sc in scores.items() if sc < 1.0)
    return {
        "agent_id": entry["agent_id"],
        "trace_id": entry["trace_id"],
        "scores": scores,
        "mean_score": mean,
        "failing_criteria": failing,
        "created_at": entry.get("created_at"),
        "threshold": threshold,
    }


def _live_failure_reason(catch: dict) -> str:
    base = (f"live catch: mean live score {catch['mean_score']:.2f} below "
            f"{catch['threshold']:g} threshold")
    if catch["failing_criteria"]:
        return base + " (failing: " + ", ".join(catch["failing_criteria"]) + ")"
    return base


def _reconstruct_input(trace: Trace) -> tuple[dict, bool]:
    """Best-effort reconstruction of a case *input* from a production trace.

    Returns ``(input, complete)``. ``complete`` is False when nothing usable
    could be recovered (e.g. a black-box trace with only a final-output span) —
    the promoted case is then additionally marked ``partial``. We only ever
    reconstruct the input; ground truth is never invented from a trace."""
    for span in trace.spans:
        if span.input:
            return dict(span.input), True
    return {}, False


def _already_promoted_live(reg: Registry, reg_id: str, trace_id: str) -> bool:
    try:
        _suite, cases = reg.get_suite(reg_id)
    except NotFoundError:
        return False
    return any(c.test_id == live_test_id(trace_id) for c in cases)


def live_catch_candidates(
    reg: Registry,
    agent_id: Optional[str] = None,
    *,
    threshold: float = DEFAULT_LIVE_CATCH_THRESHOLD,
) -> list[dict]:
    """Below-threshold sampled production traces — the live promotion sources.

    Distinct from ``promotion_candidates`` (which lists scorecards): these are
    individual live-monitor catches, each annotated with whether its input can
    be reconstructed and whether it has already been promoted."""
    out: list[dict] = []
    for entry in reg.live_trace_scores(agent_id):
        catch = _live_catch(entry, threshold)
        if catch is None:
            continue
        reg_id = regression_suite_id(catch["agent_id"], LIVE_SOURCE_SUITE)
        try:
            trace = reg.get_trace(catch["trace_id"])
            _inp, complete = _reconstruct_input(trace)
            catch["input_reconstructed"] = complete
            catch["final_output"] = (trace.final_output or "")[:200]
            catch["visibility"] = trace.visibility
        except NotFoundError:
            # scores survive but the trace payload is gone — surfaced, but its
            # input can't be reconstructed, so it promotes as needs-review/partial
            catch["input_reconstructed"] = False
            catch["final_output"] = ""
            catch["visibility"] = None
        catch["already_promoted"] = _already_promoted_live(
            reg, reg_id, catch["trace_id"])
        catch["regression_suite_id"] = reg_id
        if catch["created_at"] is not None:
            catch["created_at"] = catch["created_at"].isoformat()
        out.append(catch)
    return out


def promote_live_failures_op(
    reg: Registry,
    agent_id: str,
    *,
    trace_ids: Optional[list[str]] = None,
    rubric_id: str = "",
    threshold: float = DEFAULT_LIVE_CATCH_THRESHOLD,
) -> dict:
    """Promote below-threshold live-monitor catches into the agent's *live*
    regression suite (``regress--<agent>--live``).

    Honesty contract: a production trace has no scripted ground truth, so each
    promoted case (a) reconstructs only the input, (b) keeps ``expected=None``
    instead of inventing one, (c) is tagged ``needs-review`` (and ``partial``
    when even the input couldn't be recovered), and (d) carries the observed
    sub-threshold live scores as provenance. The suite is left **unapproved**
    so the Step-8 human gate must clear it before it can be re-run — the
    reconstructed input and chosen rubric want a human's eyes first.

    Create-or-append, version-bumped and de-duped exactly like the scorecard
    path; an optional ``trace_ids`` allowlist narrows to an explicit subset."""
    wanted = set(trace_ids) if trace_ids else None
    catches: list[dict] = []
    for entry in reg.live_trace_scores(agent_id):
        catch = _live_catch(entry, threshold)
        if catch is None:
            continue
        if wanted is not None and catch["trace_id"] not in wanted:
            continue
        catches.append(catch)

    reg_id = regression_suite_id(agent_id, LIVE_SOURCE_SUITE)
    try:
        existing_suite, existing_cases = reg.get_suite(reg_id)
        base_version = existing_suite.version
        manifest = _decode_manifest(existing_suite.business_context)
    except NotFoundError:
        existing_cases = []
        base_version = 0
        manifest = {"kind": _MANIFEST_KIND, "source_suite_id": LIVE_SOURCE_SUITE,
                    "agent_id": agent_id, "cases": {}}

    new_version = base_version + 1
    kept: list[TestCase] = [
        c.model_copy(update={"suite_id": reg_id, "version": new_version})
        for c in existing_cases
    ]
    existing_ids = {c.test_id for c in kept}
    existing_prints = {fingerprint(c) for c in kept}

    added: list[str] = []
    skipped: list[str] = []
    unresolved: list[str] = []
    for catch in catches:
        trace_id = catch["trace_id"]
        tid = live_test_id(trace_id)
        try:
            trace = reg.get_trace(trace_id)
            inp, complete = _reconstruct_input(trace)
        except NotFoundError:
            # the live score exists but the trace payload is gone: nothing to
            # reconstruct an input from — skip rather than fabricate a case
            unresolved.append(trace_id)
            continue
        tags = ["live", "needs-review"] + ([] if complete else ["partial"])
        case = TestCase(
            test_id=tid, suite_id=reg_id, version=new_version,
            task_description=("live production catch — reconstructed from a "
                              "sampled trace; review input + rubric before "
                              "trusting as a regression test"),
            input=inp,
            expected=None,            # never fabricate production ground truth
            tags=tags,
            rubric_id=rubric_id,
        )
        fp = fingerprint(case)
        if tid in existing_ids or fp in existing_prints:
            skipped.append(trace_id)
            continue
        kept.append(case)
        existing_ids.add(tid)
        existing_prints.add(fp)
        added.append(trace_id)
        manifest["cases"][tid] = {
            "why": _live_failure_reason(catch),
            "source": "live",
            "source_trace_id": trace_id,
            "observed_scores": catch["scores"],
            "failing_criteria": catch["failing_criteria"],
            "mean_score": catch["mean_score"],
            "threshold": threshold,
            "needs_review": True,
            "input_reconstructed": complete,
            "rubric_id": rubric_id,
            "fingerprint": fp,
        }

    if not added:
        return {
            "regression_suite_id": reg_id,
            "version": base_version,
            "created": False,
            "added": [],
            "skipped_duplicates": skipped,
            "unresolved_traces": unresolved,
            "needs_review": True,
            "total_cases": len(existing_cases),
            "agent_id": agent_id,
            "source": "live",
        }

    suite = TestSuite(
        suite_id=reg_id,
        version=new_version,
        business_context=_encode_manifest(manifest),
        test_ids=[c.test_id for c in kept],
        # Unapproved on purpose: reconstructed-from-production cases must pass
        # the human gate (verify input + attach a real rubric) before re-runs.
        approved=False,
    )
    reg.save_suite(suite, kept)
    return {
        "regression_suite_id": reg_id,
        "version": new_version,
        "created": base_version == 0,
        "added": added,
        "skipped_duplicates": skipped,
        "unresolved_traces": unresolved,
        "needs_review": True,
        "total_cases": len(kept),
        "agent_id": agent_id,
        "source": "live",
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
        source_suite_id = manifest.get("source_suite_id", "")
        out.append({
            "regression_suite_id": s["suite_id"],
            "version": s["version"],
            "n_cases": s["n_cases"],
            "agent_id": agent_id,
            "source_suite_id": source_suite_id,
            "source": "live" if source_suite_id == LIVE_SOURCE_SUITE else "scorecard",
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

    source_suite_id = manifest.get("source_suite_id", "")
    is_live = source_suite_id == LIVE_SOURCE_SUITE
    return {
        "regression_suite_id": regression_suite_id,
        "version": suite.version,
        "agent_id": agent_id,
        "source_suite_id": source_suite_id,
        "source": "live" if is_live else "scorecard",
        # live suites stay unapproved until a human verifies the reconstructed
        # cases; surface it so the UI can show the gate + a needs-review banner
        "approved": suite.approved,
        "needs_review": is_live and not suite.approved,
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
