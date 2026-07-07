# Regulatory Crosswalk — Dossier Artifacts → Clause Families

This maps the evidence artifacts inside an Agenttic certification **dossier** onto
the clause *families* of the three frontier-AI governance regimes Agenttic tracks:
the **EU GPAI Code of Practice (CoP)**, **California SB 53**, and the
**New York RAISE Act**.

> **Disclaimer.** This crosswalk is provided as an *evidence aid*. A dossier is
> **evidence, not a compliance determination**. Whether any obligation is met
> under any regime is a legal judgment for the operator and their counsel.
> Agenttic makes no representation that a certified agent is compliant.

| Dossier artifact | EU GPAI CoP | CA SB 53 | NY RAISE Act |
| --- | --- | --- | --- |
| Tier decision + caps (`tier_decision`) | Systemic-risk assessment outcome | Safety determination | Safety evaluation result |
| Domain coverage + NOT ASSESSED honesty (`coverage`) | Risk-assessment scope & gaps | Assessed vs. unassessed capabilities | Evaluation scope disclosure |
| Metric scorecards (`scorecard_refs`) | Model evaluation evidence | Capability testing evidence | Testing evidence |
| Elicitation gap analysis (`elicitation`) | Adversarial / elicitation testing | Red-team / elicitation evidence | Capability-elicitation evidence |
| Judge calibration (`calibration`) | Evaluation methodology soundness | Methodology disclosure | Methodology disclosure |
| Attestation mode (`attestation`) | First-party vs. independent evaluation | Attestation provenance | Independent-evaluation provenance |
| Hash chain (`content_sha256` / `prev_dossier_sha256`) | Record integrity & traceability | Tamper-evident recordkeeping | Auditable recordkeeping |
| Incident export (see INCIDENT_CROSSWALK.md) | Serious-incident reporting | Safety-incident reporting | Safety-incident disclosure |
| Certification status (`current`/`stale`/`revoked`) | Ongoing-monitoring evidence | Continued-validity disclosure | Continued-validity disclosure |

## Clause-family notes
- **NOT ASSESSED is a feature, not a gap to hide.** cbrn_proxy stays NOT ASSESSED
  by design (Hard Rule 10); the crosswalk surfaces it as an explicit scope
  limitation, which is exactly what each regime's scope-disclosure clause expects.
- **Independent attestation** (evaluator role) maps to the independent-evaluation
  provisions; self-attested dossiers are labeled as such and never dressed up as
  independent (Hard Rule 13).

See also: [INCIDENT_CROSSWALK.md](INCIDENT_CROSSWALK.md) for incident-report field
mapping.
