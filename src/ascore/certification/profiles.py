"""Certification profile loader (SPEC-2 T12.2 / T12.3 / T12.4).

A profile is a *pinned* recipe: exact suite versions + thresholds keyed to the
metric catalog. Loading a profile:

1. reads its config block (``certification.profiles.<id>`` — min_k, required
   domains, thresholds) from config.yaml (config over code), and
2. resolves its pinned :class:`SuiteRef` list against the registry, failing
   **loudly and by name** on any suite that is unknown or unapproved
   (Hard Rule: unknown/unapproved ref fails loudly named).

The same pinned refs re-resolve byte-identically on every call (T12.6), which is
what makes a dossier reproducible.
"""

from __future__ import annotations

from ascore.certification.domains import (
    DOMAIN_THRESHOLD_KEY,
    domain_for_suite,
    suite_provenance,
    suites_for_domain,
)
from ascore.metrics.catalog import METRICS
from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.certification import (
    CAPABILITY_DOMAINS,
    CertificationProfile,
    SuiteRef,
)

_METRIC_IDS = {m.id for m in METRICS}


class ProfileError(ValueError):
    """A certification profile could not be resolved. The message always names
    the offending profile / suite / threshold key."""


def profile_config(cfg: dict, profile_id: str) -> dict:
    profiles = (cfg or {}).get("certification", {}).get("profiles", {})
    if profile_id not in profiles:
        raise ProfileError(
            f"profile {profile_id!r} is not defined in "
            f"certification.profiles (known: {sorted(profiles)})"
        )
    return profiles[profile_id]


def _validate_thresholds(profile_id: str, thresholds: dict) -> None:
    # threshold keys are keyed to the metric catalog OR to the domain threshold
    # keys (tool_use_score is a domain key, not a raw metric id).
    allowed = _METRIC_IDS | {
        k for k in DOMAIN_THRESHOLD_KEY.values() if k is not None
    }
    for key in thresholds:
        if key not in allowed:
            raise ProfileError(
                f"profile {profile_id}: threshold key {key!r} is not a known "
                f"metric-catalog / domain key"
            )


def build_profile(cfg: dict, reg, profile_id: str, *, version: int = 1
                  ) -> CertificationProfile:
    """Construct a :class:`CertificationProfile` from config, pinning the exact
    versions of every catalog suite (std seeds + ingested datasets) that maps to
    one of the profile's required domains and is present + approved in ``reg``."""
    pc = profile_config(cfg, profile_id)
    required_domains = list(pc.get("required_domains", []))
    thresholds = dict(pc.get("thresholds", {}))
    _validate_thresholds(profile_id, thresholds)

    suite_refs: list[SuiteRef] = []
    for domain in required_domains:
        for suite_id in suites_for_domain(domain):
            try:
                suite, _cases = reg.get_suite(suite_id)
            except NotFoundError:
                continue  # not ingested in this workspace; coverage handles it
            if not suite.approved:
                continue  # unapproved suites never pin into a profile
            suite_refs.append(SuiteRef(suite_id=suite_id, version=suite.version))

    caveats = list(pc.get("caveats", [])) or _default_caveats(required_domains)
    floors = dict((cfg or {}).get("certification", {})
                  .get("tiers", {}).get("floors", {}))
    return CertificationProfile(
        profile_id=profile_id,
        version=version,
        description=pc.get("description", ""),
        suite_refs=suite_refs,
        required_domains=required_domains,
        min_k=int(pc.get("min_k", 1)),
        thresholds=thresholds,
        floors=floors,
        caveats=caveats,
    )


def _default_caveats(required_domains: list[str]) -> list[str]:
    """Honesty caveats generated verbatim from domain provenance. Unbacked
    domains (no real dataset) are declared, cbrn_proxy loudest of all."""
    out: list[str] = []
    for domain in required_domains:
        if domain == "cbrn_proxy":
            out.append(
                "cbrn_proxy: NOT ASSESSED — no CBRN evaluation is performed; "
                "no novel harmful content is generated (Hard Rule 10)."
            )
        elif not suites_for_domain(domain):
            out.append(
                f"{domain}: NOT ASSESSED — no dataset or seed suite backs this "
                f"domain in the default catalog."
            )
    return out


def resolve_suite_refs(profile: CertificationProfile, reg) -> list:
    """Re-resolve every pinned suite ref, failing loudly and by name on any
    suite that is unknown or unapproved. Returns the resolved suites."""
    resolved = []
    for ref in profile.suite_refs:
        try:
            suite, _cases = reg.get_suite(ref.suite_id, ref.version)
        except NotFoundError as exc:
            raise ProfileError(
                f"profile {profile.profile_id}: pinned suite "
                f"{ref.suite_id}@v{ref.version} is unknown in this registry"
            ) from exc
        if not suite.approved:
            raise ProfileError(
                f"profile {profile.profile_id}: pinned suite "
                f"{ref.suite_id}@v{ref.version} is present but UNAPPROVED"
            )
        resolved.append(suite)
    return resolved


def load_profile(cfg: dict, reg, profile_id: str) -> CertificationProfile:
    """Load a profile: prefer a persisted (pinned) version in the registry;
    otherwise build it from config. Either way, re-resolve the pins and fail
    loudly on any unknown/unapproved suite."""
    try:
        profile = reg.get_profile(profile_id)
    except NotFoundError:
        profile = build_profile(cfg, reg, profile_id)
    resolve_suite_refs(profile, reg)
    return profile
