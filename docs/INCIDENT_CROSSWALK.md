# Incident Export — Regulatory Field Crosswalk

`Incident.export()` emits a stable `agenttic-incident-export/v1` JSON object. This
document maps each export field to the corresponding disclosure field in the
three frontier-AI safety-incident regimes Agenttic tracks. **This crosswalk is an
evidence aid, not a compliance determination.** Whether a given incident is
reportable under any regime is a legal judgment for the operator.

| Export field | CA SB 53 (safety incident report) | NY RAISE Act (safety incident disclosure) | EU GPAI Code of Practice (serious-incident report) |
| --- | --- | --- | --- |
| `incident_id` | Incident identifier | Incident reference | Incident reference number |
| `affected_system` | Frontier model / system | Frontier model | Model / system concerned |
| `severity` | Severity classification | Severity | Seriousness classification |
| `status` | Report status (open→closed) | Handling status | Lifecycle status |
| `summary` | Description of the incident | Nature of the incident | Description of the serious incident |
| `origin` | How discovered | Detection source | Source of detection |
| `discovered_at` | Date of discovery | Time of occurrence/discovery | Date/time became aware |
| `sla_due_at` | Reporting deadline | Disclosure timeline | Reporting deadline |
| `closed_at` | Resolution date | Closure date | Date of resolution |
| `evidence_refs` | Supporting evidence | Supporting documentation | Evidence / supporting material |
| `trace_refs` | Technical artifacts | Technical artifacts | Technical evidence pointers |

## Severity → SLA
SLA hours per severity come from `config.incidents.sla_hours`
(S1/S2 = 72h, S3 = 168h, S4 = 336h by default) and set `sla_due_at`. Operators
should reconcile these defaults against the actual statutory deadline of each
regime for the incident's severity.

## Notes
- The export never includes payloads or PII by default; only hashes, refs, and
  aggregate metadata (Hard Rule 30).
- `status` and `sla_due_at` are computed (from the append-only event stream and
  the SLA clock), never hand-edited.
