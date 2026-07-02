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

from dataclasses import dataclass

from ascore.metrics.swebench_resolve import HARNESS_ENV, harness_available


@dataclass(frozen=True)
class WedgeReproduction:
    wedge: str                 # short id ("code", "tool_calling", ...)
    label: str                 # human label
    benchmark: str             # the public benchmark it targets
    official_metric: str       # the metric that benchmark's leaderboard reports
    status: str                # "reproduced" | "proxy" | "seed_sample"
    reproduced: bool           # True only when it reproduces a PUBLIC number
    scored_by: str             # what actually produces our number today
    requires: str              # what reproducing the public number needs
    reason: str                # one-line honest explanation


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
        WedgeReproduction(
            wedge="tool_calling", label="Tool-calling (BFCL v3 / τ-bench)",
            benchmark="Berkeley Function-Calling Leaderboard / τ-bench",
            official_metric="AST accuracy / task success",
            status="seed_sample", reproduced=False,
            scored_by="real adapters over tiny vendored samples (AST / trajectory)",
            requires="a model API key to generate predictions over the full public split",
            reason=("The scorers are the real methodology, but we ship a small "
                    "vendored sample, not the public split, and run no model "
                    "without your key — so no published model number is reproduced.")),
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
        "summary": ("No wedge reproduces a public leaderboard number in this "
                    "environment yet: model-scored wedges need an API key + the "
                    "full split, and the code wedge needs the Docker resolve-rate "
                    "harness. Status is shown per wedge rather than hidden."),
        "wedges": [
            {
                "wedge": w.wedge, "label": w.label, "benchmark": w.benchmark,
                "official_metric": w.official_metric, "status": w.status,
                "reproduced": w.reproduced, "scored_by": w.scored_by,
                "requires": w.requires, "reason": w.reason,
            } for w in wedges
        ],
    }
