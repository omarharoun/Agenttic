"""Domain coverage computation (SPEC-2 T12.4).

For each of a profile's required domains, decide the honesty stance from the
*dataset provenance* actually present in the workspace (Hard Rule 9):

* a real, ingested public dataset backing the domain  → ``assessed_real``
* only agenttic seed / placeholder data               → ``assessed_seed``
* nothing mapping to the domain                        → ``not_assessed``

Placeholders/seed suites are NEVER reported as ``assessed_real``; unmapped
domains (cbrn_proxy on the default catalog) are NEVER estimated — they stay
``not_assessed`` and a caveat is emitted verbatim.
"""

from __future__ import annotations

from ascore.certification.domains import suite_provenance, suites_for_domain
from ascore.registry.sqlite_store import NotFoundError
from ascore.schema.certification import DomainCoverage

# ordering so we can take the "best" provenance a domain actually has
_RANK = {"not_assessed": 0, "assessed_seed": 1, "assessed_real": 2}


def domain_coverage(reg, domain: str, *, include_swe: bool = False) -> DomainCoverage:
    """Coverage for one domain, computed from suites present in ``reg``."""
    best_status = "not_assessed"
    evidence: list[str] = []
    for suite_id in suites_for_domain(domain, include_swe=include_swe):
        try:
            suite, _cases = reg.get_suite(suite_id)
        except NotFoundError:
            continue
        prov = suite_provenance(suite_id, suite)
        evidence.append(f"suite:{suite_id}@v{suite.version}")
        if _RANK[prov] > _RANK[best_status]:
            best_status = prov

    note = None
    if best_status == "not_assessed":
        if domain == "cbrn_proxy":
            note = ("NOT ASSESSED — no CBRN evaluation performed; no novel "
                    "harmful content generated (Hard Rule 10)")
        else:
            note = "NOT ASSESSED — no dataset or seed suite present for this domain"
    elif best_status == "assessed_seed":
        note = "seed data only — not a real ingested benchmark"

    # not_assessed carries no fabricated evidence numbers
    return DomainCoverage(
        domain=domain,
        status=best_status,
        evidence_refs=evidence if best_status != "not_assessed" else [],
        note=note,
    )


def coverage(reg, profile) -> list[DomainCoverage]:
    """Coverage across all of a profile's required domains, in profile order."""
    include_swe = getattr(profile, "pack", None) == "swe"
    return [domain_coverage(reg, d, include_swe=include_swe)
            for d in profile.required_domains]
