"""Capability-domain tags for suites, datasets, and metrics (SPEC-2 T12.1).

A certification profile requires evidence across eight capability domains. This
module is the single source of truth mapping the platform's suites/datasets and
canonical metrics onto those domains, plus each domain's *dataset provenance*
(does a real, ingested dataset back it, only seed data, or nothing at all?).

Domain tags are implemented as a deterministic catalog mapping rather than a
mutable field on each :class:`TestSuite`: suites are append-only and immutable,
and the mapping is a pure function of ``suite_id``. Coverage computation
(``certification/profiles.py``) reads this to decide assessed_real /
assessed_seed / not_assessed per domain (Hard Rule 9).
"""

from __future__ import annotations

from ascore.schema.certification import CAPABILITY_DOMAINS

# -- domain -> canonical metric ids ----------------------------------------- #

DOMAIN_METRICS: dict[str, tuple[str, ...]] = {
    "tool_use": ("tool_call_accuracy",),
    "reliability": ("reliability_pass_k",),
    "faithfulness": ("faithfulness",),
    "harm_refusal": ("harmful_refusal_rate",),
    "injection_robustness": ("injection_robustness",),
    # proxy / probe domains have no committed index metric yet
    "autonomy_proxy": (),
    "deception_probe": (),
    "cbrn_proxy": (),
}

# The profile-config threshold key each domain gates on (metric-catalog keyed).
DOMAIN_THRESHOLD_KEY: dict[str, str | None] = {
    "tool_use": "tool_use_score",
    "reliability": "reliability_pass_k",
    "faithfulness": "faithfulness",
    "harm_refusal": "harmful_refusal_rate",
    "injection_robustness": "injection_robustness",
    "autonomy_proxy": None,
    "deception_probe": None,
    "cbrn_proxy": None,
}

# -- suite_id -> domain ----------------------------------------------------- #
# Standard seed suites (agenttic's own seed data → assessed_seed).
_STD_SUITE_DOMAIN: dict[str, str] = {
    "std-tool-use-v1": "tool_use",
    "std-safety-refusal-v1": "harm_refusal",
    "std-safety-injection-v1": "injection_robustness",
    "std-faithfulness-v1": "faithfulness",
}

# Ingested public-dataset suites (real data → assessed_real once present).
_DATASET_SUITE_DOMAIN: dict[str, str] = {
    "bfcl-simple-v3": "tool_use",
    "bfcl-parallel-v3": "tool_use",
    "bfcl-multiple-v3": "tool_use",
    "bfcl-parallel-multiple-v3": "tool_use",
    "bfcl-live-simple-v3": "tool_use",
    "bfcl-live-multiple-v3": "tool_use",
    "tau-bench-v1": "tool_use",
    "agentharm-harmful-v1": "harm_refusal",
    "injecagent-v1": "injection_robustness",
    "agentdojo-v1": "injection_robustness",
    "assistantbench-v1": "faithfulness",
    "gaia-v1": "reliability",
    "swebench-verified-v1": "reliability",
}


def domain_for_suite(suite_id: str) -> str | None:
    """The capability domain a suite belongs to, or None if untagged."""
    if suite_id in _STD_SUITE_DOMAIN:
        return _STD_SUITE_DOMAIN[suite_id]
    return _DATASET_SUITE_DOMAIN.get(suite_id)


def suite_provenance(suite_id: str) -> str:
    """'assessed_real' for ingested public datasets, 'assessed_seed' for the
    std- seed suites, 'not_assessed' for anything untagged."""
    if suite_id in _DATASET_SUITE_DOMAIN:
        return "assessed_real"
    if suite_id in _STD_SUITE_DOMAIN:
        return "assessed_seed"
    return "not_assessed"


def suites_for_domain(domain: str, *, include_datasets: bool = True) -> list[str]:
    out = [s for s, d in _STD_SUITE_DOMAIN.items() if d == domain]
    if include_datasets:
        out += [s for s, d in _DATASET_SUITE_DOMAIN.items() if d == domain]
    return out


# Domains with NO real or seed suite anywhere in the catalog. These can only
# ever be not_assessed on the default catalog — cbrn_proxy stays NOT ASSESSED by
# design (Hard Rule 10: no novel harmful content); autonomy_proxy /
# deception_probe are proxy probes with no committed dataset yet.
UNBACKED_DOMAINS: tuple[str, ...] = (
    "autonomy_proxy",
    "deception_probe",
    "cbrn_proxy",
)


def all_domains() -> tuple[str, ...]:
    return CAPABILITY_DOMAINS
