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

from agenttic.metrics.swebench_resolve import HARNESS_ENV, harness_available


@dataclass(frozen=True)
class WedgeReproduction:
    wedge: str                 # short id ("code", "tool_calling", ...)
    label: str                 # human label
    benchmark: str             # the public benchmark it targets
    official_metric: str       # the metric that benchmark's leaderboard reports
    # "reproduced"          — reproduces a published per-model number LIVE, now
    # "reproduced_recorded" — a real run reproduced the number on a recorded date/
    #                         commit; an attested historical figure, NOT re-measured
    #                         live here (see `recorded` + evidence in `extra`)
    # "attempted"           — a real model run was scored against a published number
    #                         but landed outside our interval (honest near-miss)
    # "scorer_validated"    — the deterministic grader is proven on real data (an
    #                         oracle scores 100%); only model predictions are missing
    # "proxy"               — an offline proxy stands in for the official metric
    # "seed_sample"         — real methodology on a vendored sample, not the split
    status: str
    reproduced: bool           # True only when it reproduces a PUBLIC number LIVE
    scored_by: str             # what actually produces our number today
    requires: str              # what reproducing the public number needs
    reason: str                # one-line honest explanation
    #: True when a recorded (historical, attested) run reproduced the number, even
    #: though it is not being re-measured live now. Kept distinct from ``reproduced``
    #: so a recorded figure never masquerades as a fresh live measurement.
    recorded: bool = False
    extra: dict = field(default_factory=dict)  # wedge-specific detail (evidence)


#: Recorded, reproducible full-split validation of the BFCL AST grader — the
#: oracle (ground-truth) predictions score 100% over the WHOLE real `simple`
#: category. Deterministic; reproduce with the command in `reproduce_cmd`.
_BFCL_FULL_VALIDATION = {
    "split": "simple", "n": 400, "accuracy": 1.0,
    "wilson_low": 0.9905, "wilson_high": 1.0, "ci_level": 0.95,
    "reproduce_cmd": "uv run agenttic reproduce-bfcl --split simple --full",
    "note": "oracle (ground-truth) predictions over the full real BFCL simple "
            "category; a correct AST grader must score 100%.",
}

#: A REAL, SUCCESSFUL reproduction: Claude Sonnet 4.5 (native function-calling,
#: temperature 0) run over the full V4 Python `simple` split, scored with our
#: FAITHFUL PORT of BFCL's official AST checker (metrics.bfcl_ast_official —
#: string normalisation, int→float, optional/unexpected-param handling; validated
#: to score the gold oracle 100% and to still reject wrong answers). The published
#: 97.75% falls INSIDE our 95% Wilson interval, so this reproduces the number.
#: (Our simpler homegrown grader scored the SAME predictions 93.75% — the ~4-point
#: gap was entirely the grader, i.e. BFCL's documented normalisation, not the
#: model. We report both for transparency.)
_BFCL_REPRODUCTION_ATTEMPT = {
    "status": "reproduced_recorded",
    "recorded": True,
    "live": False,
    "recorded_commit": "2aa8a68",
    "model": "claude-sonnet-4-5-20250929",
    "mode": "native function-calling (FC), temperature 0",
    "dataset": "BFCL V4 simple_python (real, n=400)",
    "grader": "faithful port of BFCL's official AST checker "
              "(metrics.bfcl_ast_official)",
    "reproduced_accuracy": 0.975,
    "n": 400, "passes": 390,
    "wilson_low": 0.9546, "wilson_high": 0.9864, "ci_level": 0.95,
    "published_accuracy": 0.9775,
    "published_metric": "Python Simple AST (FC)",
    "published_source": "BFCL V4 leaderboard, data_non_live.csv "
                        "(gorilla.cs.berkeley.edu/leaderboard.html)",
    "published_within_interval": True,
    "gap": 0.0025,
    "homegrown_grader_accuracy": 0.9375,
    "run_date": "2026-07-03",
    "reproduce_cmd": "uv run agenttic reproduce-bfcl --live --model "
                     "claude-sonnet-4-5-20250929 --published 0.9775",
    "note": "RECORDED reproduction (run 2026-07-03, commit 2aa8a68; an attested "
            "historical figure, not re-measured live here): 97.50% (390/400, "
            "Wilson95 [0.9546,0.9864]) vs the "
            "published 97.75% — published falls inside our interval (gap 0.25 pts). "
            "Scored with a faithful port of BFCL's official AST checker (validated: "
            "gold oracle scores 100%, wrong answers still rejected). Anti-gaming: "
            "the port credits only BFCL's documented normalisations and still fails "
            "10/400 genuinely-wrong model answers.",
}


def _tool_calling_wedge() -> WedgeReproduction:
    """BFCL tool-calling wedge. The deterministic AST grader is VALIDATED live on
    real data here (oracle → 100%). The published per-model number was reproduced
    in a RECORDED run (2026-07-03, commit 2aa8a68) but is NOT re-measured live in
    this environment (no model key), so ``reproduced`` (live) is False and the
    recorded figure is surfaced as an attested historical value (``recorded``)."""
    from agenttic.metrics.bfcl_reproduce import validate_scorer
    # Live, offline, network-free grader check on the real vendored sample.
    try:
        sample = validate_scorer("simple").to_dict()
    except Exception:  # noqa: BLE001 — a public read must never 500
        sample = None
    return WedgeReproduction(
        wedge="tool_calling", label="Tool-calling (BFCL — Berkeley Function Calling)",
        benchmark="Berkeley Function-Calling Leaderboard (BFCL)",
        official_metric="AST accuracy",
        # A RECORDED run reproduced the published Python Simple AST number; not a
        # live re-measurement here. reproduced (live) stays False; recorded=True.
        status="reproduced_recorded",
        reproduced=False,
        recorded=True,
        scored_by="faithful port of BFCL's official AST checker "
                  "(metrics.bfcl_ast_official; oracle scores 100% LIVE here, wrong "
                  "answers rejected). The published-number reproduction is a "
                  "recorded run, not re-measured live.",
        requires="a live re-run to re-measure: `agenttic reproduce-bfcl --live` with "
                 "a model API key",
        reason=("RECORDED (2026-07-03, commit 2aa8a68; an attested historical "
                "figure, not re-measured live here): Claude Sonnet 4.5 (native "
                "function-calling, temp 0) over the real V4 Python `simple` split "
                "(n=400) scored with a faithful port of BFCL's OFFICIAL AST "
                "checker: 97.50% (390/400, Wilson95 [0.9546,0.9864]) — the "
                "published 97.75% falls INSIDE that interval (gap 0.25 pts). The "
                "grader is re-validated LIVE here (oracle 100%); re-run "
                "`agenttic reproduce-bfcl --live` to reproduce the model number."),
        extra={
            "scorer_validation_sample": sample,
            "scorer_validation_full_split": _BFCL_FULL_VALIDATION,
            "model_reproduction": _BFCL_REPRODUCTION_ATTEMPT,
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
    """The honest, machine-readable reproduction status of every wedge.

    ``any_reproduced`` is the LIVE sense — did any wedge reproduce a public number
    in THIS environment right now (needs a model key / execution harness). It is
    False here. ``any_reproduced_recorded`` is True when a recorded, attested
    historical run reproduced a number (BFCL) even though it isn't re-measured
    live — surfaced so the figure is credited without being passed off as live."""
    wedges = _wedges()
    return {
        "any_reproduced": any(w.reproduced for w in wedges),
        "any_reproduced_recorded": any(w.reproduced or w.recorded for w in wedges),
        "summary": ("BFCL tool-calling has a RECORDED reproduction of a public "
                    "leaderboard number (run 2026-07-03, commit 2aa8a68): Claude "
                    "Sonnet 4.5 (native FC, temp 0) over the n=400 Python simple "
                    "split scored 97.50% (Wilson95 [0.9546,0.9864]) with a faithful "
                    "port of BFCL's official AST checker — the published 97.75% "
                    "falls inside the interval. That is an attested historical "
                    "figure, NOT re-measured live here (no model key); the AST "
                    "grader IS re-validated live (oracle 100%). The code wedge "
                    "(SWE-bench) needs the Docker resolve-rate harness and stays a "
                    "proxy; model-scored wedges without a live run stay seed-sample. "
                    "Status (incl. recorded-vs-live) is shown per wedge, not hidden."),
        "wedges": [
            {
                "wedge": w.wedge, "label": w.label, "benchmark": w.benchmark,
                "official_metric": w.official_metric, "status": w.status,
                "reproduced": w.reproduced, "recorded": w.recorded,
                "scored_by": w.scored_by,
                "requires": w.requires, "reason": w.reason,
                **({"detail": w.extra} if w.extra else {}),
            } for w in wedges
        ],
    }
