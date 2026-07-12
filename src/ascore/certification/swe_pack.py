"""cert-swe-v1 pack manifest — the honest, generated coverage/provenance view.

This turns the *actual* profile config + registry state into a per-domain
coverage manifest (assessed_real / assessed_seed / NOT ASSESSED), the pinned
suites, their provenance (author / source / date / held-out), and the pack's
weights/thresholds/floors. It is the single source the pack README + COVERAGE.md
render from, so the docs can never drift from what the engine actually does.

Nothing here computes a score — it describes the recipe. A pass under this pack
means the agent RESISTED these specific probes, not that it writes secure code.
"""

from __future__ import annotations

from ascore.certification.coverage import coverage as compute_coverage
from ascore.certification.domains import suites_for_domain
from ascore.certification.profiles import load_profile
from ascore.metrics.swe_suites import SWE_SUITE_PROVENANCE

PROFILE_ID = "cert-swe-v1"

CLAIM = (
    "cert-swe-v1 attests that this agent version resisted the specific SWE "
    "safety probes in this profile — a measurement of tested behavior, not a "
    "guarantee of secure code or a security audit. Domains outside this profile "
    "are marked NOT ASSESSED."
)

#: Which layer each required domain belongs to (doc/reporting only).
DOMAIN_LAYER: dict[str, str] = {
    "secret_exfiltration": "A (domain safety)",
    "destructive_ops": "A (domain safety)",
    "vuln_introduction": "A (domain safety)",
    "dependency_safety": "A (domain safety)",
    "supply_chain_ci": "A (domain safety)",
    "license_leak": "A (domain safety)",
    "tool_use": "B (competence — reused)",
    "reliability": "B (competence — reused)",
    "faithfulness": "B (competence — reused)",
    "injection_robustness": "C (attack surface)",
}


def pack_manifest(cfg: dict, reg, profile_id: str = PROFILE_ID) -> dict:
    """Build the pack coverage/provenance manifest from config + registry.

    Requires the pack suites to be seeded in ``reg`` for accurate coverage
    (``metrics.swe_suites.seed_swe_suites``)."""
    profile = load_profile(cfg, reg, profile_id)
    include_swe = getattr(profile, "pack", None) == "swe"
    cov = {c.domain: c for c in compute_coverage(reg, profile)}

    domains = []
    for dom in profile.required_domains:
        suites = suites_for_domain(dom, include_swe=include_swe)
        prov = {s: SWE_SUITE_PROVENANCE[s] for s in suites
                if s in SWE_SUITE_PROVENANCE}
        c = cov.get(dom)
        domains.append({
            "domain": dom,
            "layer": DOMAIN_LAYER.get(dom, "—"),
            "suites": suites,
            "coverage": c.status if c else "not_assessed",
            "note": (c.note if c else None),
            "weight": profile.weights.get(dom)
                or profile.weights.get(_metric_key(dom)),
            "threshold": profile.thresholds.get(dom)
                or profile.thresholds.get(_metric_key(dom)),
            "floor": profile.floors.get(dom) or profile.floors.get(_metric_key(dom)),
            "provenance": prov,
        })

    return {
        "profile_id": profile.profile_id,
        "version": profile.version,
        "label": "Software-Engineering Agent Safety",
        "min_k": profile.min_k,
        "claim": CLAIM,
        "caveats": list(profile.caveats),
        "weights": dict(profile.weights),
        "thresholds": dict(profile.thresholds),
        "floors": dict(profile.floors),
        "domains": domains,
    }


# a few domains gate on a differently-named metric key (tool_use -> the
# tool_call_accuracy metric; reliability -> reliability_pass_k).
_METRIC_KEY = {"tool_use": "tool_call_accuracy", "reliability": "reliability_pass_k"}


def _metric_key(domain: str) -> str:
    return _METRIC_KEY.get(domain, domain)


def render_markdown(manifest: dict) -> str:
    """Render the manifest as the COVERAGE.md table (honest, generated)."""
    out: list[str] = []
    out.append(f"# {manifest['label']} — coverage manifest (`{manifest['profile_id']}` "
               f"v{manifest['version']})")
    out.append("")
    out.append("> **This file is generated** by `scripts/gen_swe_manifest.py` from "
               "the live profile + registry. Do not edit by hand.")
    out.append("")
    out.append(f"**Claim.** {manifest['claim']}")
    out.append("")
    out.append(f"`min_k = {manifest['min_k']}`. A **floor** breach caps the tier at "
               "**C**; a **threshold** miss caps at **B**. Weights are the pack's "
               "composite reweighting toward coding-agent powers.")
    out.append("")
    out.append("| Domain | Layer | Coverage | Weight | Threshold | Floor | Suites |")
    out.append("|---|---|---|---|---|---|---|")
    for d in manifest["domains"]:
        cov = {"assessed_real": "assessed_real", "assessed_seed": "assessed_seed",
               "not_assessed": "**NOT ASSESSED**"}.get(d["coverage"], d["coverage"])
        w = f"{d['weight']:.2f}" if d["weight"] is not None else "—"
        t = f"{d['threshold']:.2f}" if d["threshold"] is not None else "—"
        fl = f"{d['floor']:.2f}" if d["floor"] is not None else "—"
        suites = ", ".join(f"`{s}`" for s in d["suites"]) or "_(none present)_"
        out.append(f"| `{d['domain']}` | {d['layer']} | {cov} | {w} | {t} | {fl} | "
                   f"{suites} |")
    out.append("")
    out.append("## Authored-suite provenance (versioned registry)")
    out.append("")
    out.append("| Suite | Author | Source | Authored | Held-out |")
    out.append("|---|---|---|---|---|")
    seen: set[str] = set()
    for d in manifest["domains"]:
        for sid, p in d["provenance"].items():
            if sid in seen:
                continue
            seen.add(sid)
            out.append(f"| `{sid}` | {p['author']} | {p['source']} | "
                       f"{p['authored']} | {'yes' if p['held_out'] else 'no'} |")
    out.append("")
    out.append("## Caveats (verbatim, carried into every dossier)")
    out.append("")
    for c in manifest["caveats"]:
        out.append(f"- {c}")
    out.append("")
    return "\n".join(out)
