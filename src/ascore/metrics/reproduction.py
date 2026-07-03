"""Per-wedge reproduction status — the honest answer to "does this number
reproduce a public benchmark result?" (review #9).

The product thesis is credibility, so every wedge states plainly whether its
number is an **official reproduced** benchmark result, a **proxy** for one, or a
**seed-sample** demonstration of the methodology — and, if not reproduced, what it
would actually take. This is surfaced end-to-end (``GET /api/public/reproduction``)
so the UI can stop hiding the caveats the docs already admit.

HONESTY: as of now, **no wedge reproduces a public leaderboard number in this
environment**, and we say so. Two independent blockers:

* Every model-scored wedge (BFCL / τ-bench tool-calling, GAIA / AssistantBench
  web-agent, AgentHarm safety) needs a **model API key** to generate predictions
  over the **full public split** — we ship the real adapters + *tiny vendored
  samples*, not the split, and the Index is empty until a user runs their own key.
* The code wedge (SWE-bench Verified) needs the **Docker execution harness** to
  compute the official *resolve-rate*; here it is scored by a documented offline
  proxy. The harness is wired but gated (``metrics.swebench_resolve``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ascore.metrics.swebench_resolve import HARNESS_ENV, harness_available


@dataclass(frozen=True)
class WedgeReproduction:
    wedge: str                 # short id ("code", "tool_calling", ...)
    label: str                 # human label
    benchmark: str             # the public benchmark it targets
    official_metric: str       # the metric that benchmark's leaderboard reports
    # "reproduced"        — reproduces a published per-model number
    # "scorer_validated"  — the deterministic grader is proven on real data (an
    #                       oracle scores 100%); only model predictions are missing
    # "proxy"             — an offline proxy stands in for the official metric
    # "seed_sample"       — real methodology on a vendored sample, not the split
    status: str
    reproduced: bool           # True only when it reproduces a PUBLIC number
    scored_by: str             # what actually produces our number today
    requires: str              # what reproducing the public number needs
    reason: str                # one-line honest explanation
    extra: dict = field(default_factory=dict)  # wedge-specific detail (evidence)


#: Recorded, reproducible full-split validation of the BFCL AST grader — the
#: oracle (ground-truth) predictions score 100% over the WHOLE real `simple`
#: category. Deterministic; reproduce with the command in `reproduce_cmd`.
_BFCL_FULL_VALIDATION = {
    "split": "simple", "n": 400, "accuracy": 1.0,
    "wilson_low": 0.9905, "wilson_high": 1.0, "ci_level": 0.95,
    "reproduce_cmd": "uv run ascore reproduce-bfcl --split simple --full",
    "note": "oracle (ground-truth) predictions over the full real BFCL simple "
            "category; a correct AST grader must score 100%.",
}


def _tool_calling_wedge() -> WedgeReproduction:
    """BFCL tool-calling wedge. The deterministic AST grader is VALIDATED on real
    data (oracle → 100%); only the model's predictions (a key) are missing to
    reproduce a published per-model number. Honest: reproduced stays False."""
    from ascore.metrics.bfcl_reproduce import (
        bfcl_blocker,
        model_predictions_available,
        validate_scorer,
    )
    # Live, offline, network-free grader check on the real vendored sample.
    try:
        sample = validate_scorer("simple").to_dict()
    except Exception:  # noqa: BLE001 — a public read must never 500
        sample = None
    have_key = model_predictions_available()
    return WedgeReproduction(
        wedge="tool_calling", label="Tool-calling (BFCL — Berkeley Function Calling)",
        benchmark="Berkeley Function-Calling Leaderboard (BFCL)",
        official_metric="AST accuracy",
        status="reproduced" if have_key else "scorer_validated",
        reproduced=False,  # no published per-model number reproduced without a key
        scored_by="deterministic AST match (our checker; validated against the "
                  "real BFCL ground truth — oracle scores 100%)",
        requires="a model API key (ANTHROPIC_API_KEY) to generate the model's "
                 "predictions; the grader + real data are already present",
        reason=("The BFCL grader is proven on real data (oracle → 100% on the full "
                "n=400 simple category), so the machinery that reproduces the "
                "leaderboard number is correct. The only missing input is the "
                "model's predictions, which need an API key — absent here — so no "
                "published per-model number is claimed."),
        extra={
            "scorer_validation_sample": sample,
            "scorer_validation_full_split": _BFCL_FULL_VALIDATION,
            "model_reproduction": bfcl_blocker(),
        })


def _wedges() -> list[WedgeReproduction]:
    swe_status = "reproduced" if harness_available() else "proxy"
    return [
        WedgeReproduction(
            wedge="code", label="Code (SWE-bench Verified)",
            benchmark="SWE-bench Verified", official_metric="resolve-rate",
            status=swe_status, reproduced=harness_available(),
            scored_by=("official Docker resolve-rate" if harness_available()
                       else "offline proxy (patch produced? gold files localized?)"),
            requires=(f"Docker + the `swebench` package + instance images, with "
                      f"{HARNESS_ENV}=docker"),
            reason=("Official resolve-rate requires executing FAIL_TO_PASS / "
                    "PASS_TO_PASS in per-instance containers; the harness is wired "
                    "but gated and absent on this host, so a proxy is used.")),
        _tool_calling_wedge(),
        WedgeReproduction(
            wedge="web_agent", label="Web agent (GAIA / AssistantBench)",
            benchmark="GAIA / AssistantBench",
            official_metric="normalized exact-match / fractional accuracy",
            status="seed_sample", reproduced=False,
            scored_by="official-style scorers over vendored samples",
            requires="a model API key + the full public split",
            reason=("Same as tool-calling: real scorers, seed sample, no model run "
                    "without a key.")),
        WedgeReproduction(
            wedge="safety", label="Safety (AgentHarm / AgentDojo / InjecAgent)",
            benchmark="AgentHarm / AgentDojo / InjecAgent",
            official_metric="refusal rate / attack-success-rate",
            status="seed_sample", reproduced=False,
            scored_by="lexical/deterministic proxies over seed probes; see the "
                      "red-team detector self-test for measured blind spots",
            requires="the official attack environments (execution) + a model",
            reason=("Our safety checks implement the published methodology on seed "
                    "probes; the real ASR needs the attack environments, not "
                    "lexical matching. The detector's misses are published "
                    "(/api/public/redteam/injection).")),
    ]


def reproduction_report() -> dict:
    """The honest, machine-readable reproduction status of every wedge."""
    wedges = _wedges()
    return {
        "any_reproduced": any(w.reproduced for w in wedges),
        "summary": ("No wedge reproduces a public per-model leaderboard number in "
                    "this environment yet: model-scored wedges need an API key to "
                    "generate predictions, and the code wedge needs the Docker "
                    "resolve-rate harness. The BFCL grader is, however, VALIDATED "
                    "on the full real dataset (oracle → 100%, n=400), so it is one "
                    "key away from a real reproduction. Status is shown per wedge "
                    "rather than hidden."),
        "wedges": [
            {
                "wedge": w.wedge, "label": w.label, "benchmark": w.benchmark,
                "official_metric": w.official_metric, "status": w.status,
                "reproduced": w.reproduced, "scored_by": w.scored_by,
                "requires": w.requires, "reason": w.reason,
                **({"detail": w.extra} if w.extra else {}),
            } for w in wedges
        ],
    }
